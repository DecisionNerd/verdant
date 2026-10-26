# Dataset lifecycle journeys

The target experience follows create → ingest → annotate → validate → publish → consume → evaluate. Each transition has an authorized principal, durable operation/result, exact revision context and actionable failure.

[First dataset publication](first-digest-journey.md) describes the tracer bullet. The filename is retained for existing links; document digestion is now a possible transformation, not the primary journey.

[ADAM: compare process flows and models](adam-flow-model-comparison.md) defines a six-configuration experiment journey, fair quality/performance/cost reporting, and fresh-trial versus recovery-retry behavior. It is a target experience requiring implementation.

[BETH: annotation agreement and Pareto tradeoffs](beth-truth-production-pareto.md) covers labeled train/validation/test truth, unlabeled production, independent annotation/adjudication, and cost/speed/quality frontier selection across flows and models. It is a target experience requiring implementation.

Agent and human journeys use the same domain services. UI, SDK and MCP differences are presentation/transport concerns, not different authorization or publication rules.
