import { DatasetView } from "./dataset";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { QueryClient, QueryClientProvider, useQuery, useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";

type Namespace = { id: string; name: string; access: "read" | "write" };
import { request, type Dataset, type Page } from "./api";
function initialRoute() {
  const params = new URLSearchParams(location.search);
  return { namespace: params.get("namespace") ?? "", dataset: params.get("dataset") ?? "" };
}
export function Catalog({ csrf, principal }: { csrf: string; principal: string }) {
  // Scope cache lifetime to one authenticated principal, including across logout/login.
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 15000 } } }));
  return <QueryClientProvider client={client}><CatalogView csrf={csrf} principal={principal} /></QueryClientProvider>;
}
function CatalogView({ csrf, principal }: { csrf: string; principal: string }) {
  const cache = useQueryClient();
  const [route, setRoute] = useState(initialRoute);
  const [search, setSearch] = useState("");
  const [modality, setModality] = useState("");
  const [mine, setMine] = useState(false);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const namespaces = useInfiniteQuery({ queryKey: ["namespaces"], initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => request<Page<Namespace>>(`/api/v1/namespaces?limit=100${pageParam ? `&after=${pageParam}` : ""}`, csrf),
    getNextPageParam: page => page.next_after ?? undefined });
  const namespaceItems = namespaces.data?.pages.flatMap(page => page.items) ?? [];
  useEffect(() => { const update = () => setRoute(initialRoute()); window.addEventListener("popstate", update); return () => window.removeEventListener("popstate", update); }, []);
  function navigate(namespace: string, dataset = "") {
    const query = new URLSearchParams({ namespace }); if (dataset) query.set("dataset", dataset);
    history.pushState(null, "", `/?${query}`); setRoute({ namespace, dataset }); setCursors([null]); setError("");
  }
  const selected = namespaceItems.find(n => n.id === route.namespace);
  const params = new URLSearchParams({ limit: "25", search });
  if (modality) params.set("modality", modality);
  if (mine) params.set("owner_id", principal);
  const cursor = cursors[cursors.length - 1]; if (cursor) params.set("after", cursor);
  const datasets = useQuery({ queryKey: ["datasets", route.namespace, search, modality, mine, cursor], enabled: !!selected && !route.dataset,
    queryFn: () => request<Page<Dataset>>(`/api/v1/namespaces/${route.namespace}/datasets?${params}`, csrf) });
  const columns = useMemo<ColumnDef<Dataset>[]>(() => [
    { accessorKey: "name", header: "Dataset", cell: ({ row }) => <a href={`/?namespace=${row.original.namespace_id}&dataset=${row.original.id}`} onClick={event => { event.preventDefault(); navigate(row.original.namespace_id, row.original.id); }}>{row.original.name}</a> },
    { accessorKey: "modality", header: "Modality" },
    { accessorKey: "owner_id", header: "Owner", cell: ({ getValue }) => getValue() === principal ? "You" : <span className="identifier">{String(getValue())}</span> },
    { accessorKey: "revision", header: "Metadata revision" },
  ], [principal]);
  const table = useReactTable({ data: datasets.data?.items ?? [], columns, getCoreRowModel: getCoreRowModel(), manualPagination: true });
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setSaving(true); setError("");
    try {
      const dataset = await request<Dataset>(`/api/v1/namespaces/${route.namespace}/datasets`, csrf, Object.fromEntries(form));
      await cache.invalidateQueries({ queryKey: ["datasets", route.namespace] }); setCreating(false); navigate(route.namespace, dataset.id);
    } catch (failure) { setError((failure as Error).message); } finally { setSaving(false); }
  }
  return <section className="catalog" aria-labelledby="catalog-title">
    <h1 id="catalog-title">Dataset registry</h1>
    {namespaces.isPending ? <p role="status">Loading workspaces…</p> : namespaces.isError ? <p role="alert">{namespaces.error.message} <button onClick={() => void namespaces.refetch()}>Retry</button></p> : <>
      <label>Workspace <select value={route.namespace} onChange={event => navigate(event.target.value)}><option value="">Choose a workspace</option>{namespaceItems.map(n => <option key={n.id} value={n.id}>{n.name} ({n.access})</option>)}</select></label>
      {namespaces.hasNextPage && <button disabled={namespaces.isFetchingNextPage} onClick={() => void namespaces.fetchNextPage()}>Load more workspaces</button>}
      {!namespaceItems.length && <p>No workspace access has been granted. Contact your administrator.</p>}
      {route.namespace && !selected && <p role="alert">This workspace is unavailable or access was removed.</p>}
      {selected && !route.dataset && <>
        <div className="catalog-tools"><label>Search datasets <input maxLength={200} value={search} onChange={event => { setSearch(event.target.value); setCursors([null]); }} /></label>
          <label>Modality <select value={modality} onChange={event => { setModality(event.target.value); setCursors([null]); }}><option value="">All modalities</option>{["image", "text", "document", "mixed", "audio", "video"].map(value => <option key={value}>{value}</option>)}</select></label>
          <label><input type="checkbox" checked={mine} onChange={event => { setMine(event.target.checked); setCursors([null]); }} /> Owned by me</label>
          {selected.access === "write" && <button onClick={() => setCreating(!creating)}>Create dataset</button>}
          <button onClick={() => void datasets.refetch()}>Refresh</button></div>
        {creating && <form onSubmit={event => void create(event)}><h2>Create dataset</h2><label>Name <input name="name" required maxLength={200} /></label><label>Slug <input name="slug" required maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" /></label><label>Modality <input name="modality" required maxLength={50} /></label><button disabled={saving}>Save dataset</button></form>}
        {error && <p role="alert">{error}</p>}
        {datasets.isPending ? <p role="status">Loading datasets…</p> : datasets.isError ? <p role="alert">{datasets.error.message}</p> : <>
          {datasets.isFetching && <p role="status">Refreshing datasets…</p>}
          {!datasets.data.items.length ? <p>No datasets match these filters.</p> : <div className="table-scroll"><table><caption>{datasets.data.total} matching datasets · page {cursors.length}</caption><thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => <th key={header.id} scope="col">{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead><tbody>{table.getRowModel().rows.map(row => <tr key={row.id}>{row.getVisibleCells().map(cell => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>}
          <nav aria-label="Dataset pages"><button disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>Previous</button><button disabled={!datasets.data.next_after} onClick={() => setCursors([...cursors, datasets.data.next_after])}>Next</button></nav>
        </>}
      </>}
      {selected && route.dataset && <p><a href={`/?namespace=${route.namespace}`} onClick={event => { event.preventDefault(); navigate(route.namespace); }}>Back to datasets</a></p>}
      {selected && route.dataset && <DatasetView namespace={route.namespace} id={route.dataset} csrf={csrf} writable={selected.access === "write"} />}
    </>}
  </section>;
}
