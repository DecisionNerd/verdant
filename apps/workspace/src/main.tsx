import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Workspace } from "./workspace";

createRoot(document.getElementById("root")!).render(<StrictMode><Workspace /></StrictMode>);
