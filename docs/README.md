# Reference guide

This documentation explains the system as a governed decision-support product. It is designed to make the design, security boundaries, and operational model understandable without having to inspect the implementation in detail.

## Reading map

| If you need to understand… | Read… | Diagram |
| --- | --- | --- |
| The components, their responsibilities, and their connections | [System architecture](architecture.md) | [System flow](assets/system-flow.svg) |
| What happens from case submission through routing or closure | [Decision lifecycle](decision-lifecycle.md) | [State machine](assets/decision-state.svg) |
| Which data crosses boundaries, who can act, and how activity is audited | [Security and governance](security-and-governance.md) | [Trust boundaries](assets/trust-boundaries.svg) |
| How to configure, index, deploy, and observe the service | [Integrations and operations](integrations-and-operations.md) | [System flow](assets/system-flow.svg) |
| What every runtime file, contract, adapter, test, and data asset does | [Component reference atlas](components/README.md) | [Component atlas](components/assets/component-atlas.svg) |
| How one LangGraph capability is separated into a local service | [Distributed RAG subgraph](components/rag-service.md) | [Distributed RAG topology](components/assets/distributed-rag-subgraph.svg) |

## Suggested reading order

1. Start with the architecture to place the decision and RAG services and their dependencies.
2. Continue with the lifecycle to understand the graph and its human-review pause.
3. Review security and governance before connecting real providers or enabling actions.
4. Use integrations and operations as the configuration, deployment, and troubleshooting guide.
5. Use the component reference when you want file-level behavior without reading source.

## Principles that apply throughout the design

- The service recommends and prepares a referral; it does not bind a policy or make a payment without explicit human approval.
- Incoming text is sanitized before it is persisted, retrieved, scored, or sent to an external integration.
- Hybrid retrieval filters by role before the language model can see evidence.
- Material outputs have provenance: retrieved evidence, model version, and human review.
- Critical dependency failures are surfaced as unavailability; they are not replaced by made-up decisions.
- The audit record is append-only and cryptographically chained per case.
