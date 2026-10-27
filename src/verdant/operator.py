"""Trusted server-operator recovery commands; never mount these as application routes."""

import argparse
import json
import os
from uuid import UUID

from sqlalchemy import select

from verdant.browser_sessions import DatabaseSessionStore
from verdant.domain import DomainError
from verdant.identity import SYSTEM_ACTOR_ID, IdentityService
from verdant.persistence import AuditRow, make_sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verdant server-operator identity recovery")
    parser.add_argument(
        "--actor",
        type=UUID,
        default=SYSTEM_ACTOR_ID,
        help="Audit actor UUID; default is the explicit trusted-process identity",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    provision = commands.add_parser(
        "provision", help="Bind an exact issuer/subject; never match email"
    )
    provision.add_argument("issuer")
    provision.add_argument("subject")
    provision.add_argument("--kind", choices=["human", "service"], required=True)
    grant = commands.add_parser("grant")
    grant.add_argument("principal_id", type=UUID)
    grant.add_argument("namespace_id", type=UUID)
    grant.add_argument("access", choices=["read", "write"])
    revoke = commands.add_parser("revoke-grant")
    revoke.add_argument("principal_id", type=UUID)
    revoke.add_argument("namespace_id", type=UUID)
    for name in ["disable", "enable"]:
        command = commands.add_parser(name)
        command.add_argument("principal_id", type=UUID)
    commands.add_parser("cleanup-sessions", help="Remove only expired session/attempt records")
    audit = commands.add_parser("audit")
    audit.add_argument("principal_id", type=UUID)
    args = parser.parse_args(argv)
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    service = IdentityService(sessions)
    try:
        if args.command == "provision":
            result = service.provision(
                args.issuer, args.subject, kind=args.kind, actor_id=args.actor
            )
            print(json.dumps({"principal_id": str(result)}))
        elif args.command == "grant":
            service.grant(args.principal_id, args.namespace_id, args.access, actor_id=args.actor)
        elif args.command == "revoke-grant":
            service.revoke_grant(args.principal_id, args.namespace_id, actor_id=args.actor)
        elif args.command in {"disable", "enable"}:
            service.set_enabled(args.principal_id, args.command == "enable", actor_id=args.actor)
        elif args.command == "cleanup-sessions":
            DatabaseSessionStore(sessions).cleanup_expired()
        elif args.command == "audit":
            with sessions() as session:
                rows = session.scalars(
                    select(AuditRow)
                    .where(
                        AuditRow.aggregate_id == args.principal_id,
                        AuditRow.action.like("identity.%"),
                    )
                    .order_by(AuditRow.occurred_at, AuditRow.id)
                ).all()
                print(
                    json.dumps(
                        [
                            {
                                "event_id": str(row.id),
                                "actor_id": str(row.actor_id),
                                "action": row.action,
                                "namespace_id": str(row.namespace_id) if row.namespace_id else None,
                                "payload": row.payload,
                                "occurred_at": row.occurred_at.isoformat(),
                            }
                            for row in rows
                        ]
                    )
                )
    except (DomainError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
