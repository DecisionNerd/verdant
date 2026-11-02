"""Private trusted cohort and native-contributor admission, never an HTTP route."""

import argparse
import os
from uuid import UUID

from verdant.annotations import AnnotationService
from verdant.domain import DomainError
from verdant.label_studio import LabelStudioSettings
from verdant.persistence import make_sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verdant trusted annotation admission")
    commands = parser.add_subparsers(dest="command", required=True)
    cohort = commands.add_parser("admit-cohort")
    cohort.add_argument("namespace_id", type=UUID)
    cohort.add_argument("--name", required=True)
    cohort.add_argument("--issuer", required=True)
    cohort.add_argument("--reason", required=True)
    contributor = commands.add_parser("bind-contributor")
    contributor.add_argument("cohort_id", type=UUID)
    contributor.add_argument("--issuer", required=True)
    contributor.add_argument("--subject", required=True)
    contributor.add_argument("--native-user-id", type=int, required=True)
    contributor.add_argument("--reviewer", action="store_true")
    contributor.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    service = AnnotationService(sessions)
    try:
        if args.command == "admit-cohort":
            result = service.admit_cohort(
                args.namespace_id,
                name=args.name,
                settings=LabelStudioSettings(
                    *[
                        os.environ["VERDANT_LS_" + name]
                        for name in ("BROWSER_ORIGIN", "MACHINE_ORIGIN", "IMAGE")
                    ]
                ),
                issuer=args.issuer,
                reason=args.reason,
            )
        else:
            result = service.bind_contributor(
                args.cohort_id,
                issuer=args.issuer,
                subject=args.subject,
                native_user_id=args.native_user_id,
                reviewer=args.reviewer,
                reason=args.reason,
            )
        print(result)
    except (DomainError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
