"""Explicit operator-only, one-call live qualification. Never run without a charge budget."""

import argparse
import json
import os
from uuid import UUID

from sqlalchemy import select

from verdant.flows import service_principal
from verdant.model_inputs import project_model_input
from verdant.model_profiles import ModelAdapter, ModelCallError, ModelProfileService, config_digest
from verdant.operations import (
    EffectReconciliation,
    EffectRequest,
    EffectResult,
    OperationRequest,
    OperationService,
)
from verdant.persistence import ModelProfileRow, make_sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", type=UUID, required=True)
    parser.add_argument("--profile", type=UUID, required=True)
    parser.add_argument(
        "--request-key", required=True, help="Reuse to recover; new key means fresh trial"
    )
    parser.add_argument("--authorize-charge", action="store_true", required=True)
    parser.add_argument("--max-output-tokens", type=int, required=True)
    args = parser.parse_args(argv)
    if not args.authorize_charge or not 1 <= args.max_output_tokens <= 256:
        parser.error("Authorize exactly one call and at most256 output tokens")
    sessions = make_sessions(os.environ["VERDANT_DATABASE_URL"])
    try:
        actor = service_principal(sessions)
        service = ModelProfileService(sessions)
        profile = service.get(actor, args.namespace, args.profile)
        if profile.settings.max_output_tokens > args.max_output_tokens:
            parser.error("Pinned profile output-token limit exceeds the authorized budget")
        operations = OperationService(sessions)
        operation = operations.admit(
            actor,
            args.namespace,
            OperationRequest(
                request_key=args.request_key,
                kind="model_qualification",
                inputs={
                    "profile_id": str(profile.id),
                    "config_digest": profile.config_digest,
                    "accepted_call_budget": 1,
                    "max_output_tokens": args.max_output_tokens,
                },
            ),
        )
        if operation.status == "succeeded":
            print(
                json.dumps(
                    {"operation_id": str(operation.id), "status": "succeeded", "reused": True}
                )
            )
            return
        if operation.status != "queued":
            parser.exit(1, "Existing qualification requires reconciliation; no call was replayed\n")
        operations.start(actor, args.namespace, operation.id, operation.epoch)
        value = project_model_input(b"Verdant synthetic provider qualification. Reply with OK.")
        receipt = operations.prepare_effect(
            actor,
            args.namespace,
            operation.id,
            operation.epoch,
            EffectRequest(
                kind="model.qualify",
                item=str(profile.id),
                input_revision=profile.config_digest,
                request={"input_digest": config_digest(value.model_dump())},
            ),
        )
        if receipt.status != "prepared":
            parser.exit(1, "Qualification receipt already dispatched; reconcile before recovery\n")
        with sessions.begin() as session:
            row = session.scalar(
                select(ModelProfileRow).where(
                    ModelProfileRow.id == profile.id, ModelProfileRow.namespace_id == args.namespace
                )
            )
            credential = row.credential_ref
        receipt = operations.begin_effect(
            actor, args.namespace, operation.id, operation.epoch, receipt.id
        )
        try:
            result = ModelAdapter().generate(
                profile,
                credential,
                value,
                prompt="Return a short synthetic connectivity acknowledgement.",
                output_contract="text",
            )
        except ModelCallError as exc:
            # Persist unknown before the call; no automatic second paid call is possible.
            if not exc.uncertain:
                operations.reconcile_effect(
                    actor,
                    args.namespace,
                    operation.id,
                    receipt.id,
                    EffectReconciliation(
                        expected_revision=receipt.revision,
                        outcome="absent",
                        evidence="Confirmed provider rejection: " + exc.code,
                    ),
                )
            with sessions.begin() as session:
                row = operations._locked_operation(
                    session, actor, args.namespace, operation.id, operation.epoch
                )
                row.status = "reconciliation_needed" if exc.uncertain else "failed"
                row.revision += 1
                operations._record_transition(session, actor, row)
            parser.exit(1, f"{exc.code}; operation {operation.id}; no blind replay\n")
        operations.complete_effect(
            actor,
            args.namespace,
            operation.id,
            operation.epoch,
            receipt.id,
            receipt.dispatch_id,
            EffectResult(
                result_digest=config_digest(result.model_dump()),
                provider_reference=result.provider_reference or "qualification-response",
            ),
        )
        with sessions.begin() as session:
            row = operations._locked_operation(
                session, actor, args.namespace, operation.id, operation.epoch
            )
            row.status = "succeeded"
            row.revision += 1
            operations._record_transition(session, actor, row)
        print(
            json.dumps(
                {
                    "operation_id": str(operation.id),
                    "receipt_id": str(receipt.id),
                    "profile_digest": profile.config_digest,
                    "provider": profile.provider,
                    "transport": profile.transport,
                    "endpoint": profile.endpoint,
                    "requested_model": profile.model,
                    "identity_kind": profile.identity_kind,
                    "resolved_model": result.resolved_model,
                    "provider_reference": result.provider_reference,
                    "output_digest": config_digest({"text": result.text}),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost": None,
                    "status": "succeeded",
                }
            )
        )
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
