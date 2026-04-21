# Why SolRL

## Slide 1: The Point

SolRL is a protocol for verifiable AI agent evaluation.

It runs Harbor evals and RL reward jobs inside AWS Nitro Enclaves, proves what happened with hardware attestations, and settles payment on Solana with a Token-2022 based registry.

The user gets a simple thing:

```text
submit eval -> get verified reward result -> pay only when the proof checks out
```

The operator gets a simple thing:

```text
stake token -> run Nitro job -> submit proof -> get paid
```

That is the whole game.

## Slide 2: The Problem

AI agents are becoming useful, but evaluating them is still too easy to fake.

Today an AI lab or agent builder can run thousands of evals in Docker, Daytona, Modal, or their own cloud scripts. That works when the evaluator and the compute provider are the same trusted party.

It breaks when you want a market.

In a decentralized eval network, the operator has a direct incentive to lie:

```text
claim the agent passed -> collect bounty -> skip the expensive compute
```

For RL, this is worse than a bad leaderboard. Bad rewards train bad models.

Garbage rewards in, garbage agent out. Very expensive garbage.

## Slide 3: Why Normal Containers Are Not Enough

Normal containers prove almost nothing to a buyer.

Docker, Sysbox, and similar sandboxing tools are useful for density and developer experience. They are not enough for trustless reward markets because the host still controls the kernel boundary.

If the host operator is malicious, the host can tamper with the runtime, logs, task result, network, or filesystem.

For centralized CI, that may be acceptable.

For paid RL rollouts, no.

## Slide 4: Why AWS Nitro

AWS Nitro Enclaves give us a hardware root of trust.

An enclave has no external network, no SSH, no persistent disk, and no normal host access. It talks to the parent EC2 instance through VSOCK. The Nitro Security Module signs an attestation document that includes PCR measurements and caller-provided data.

That means SolRL can bind the reward to the exact environment that produced it:

```text
Harbor task + worker image + operator + nonce + reward
        |
        v
PCR16 + ClaimV1
        |
        v
Nitro attestation
        |
        v
Verifier signature
        |
        v
Solana settlement
```

The parent EC2 instance can relay messages. It cannot forge the Nitro attestation.

## Slide 5: Why Harbor

Harbor is the right wedge because evals are the bottleneck.

Agent builders do not need another generic compute marketplace first. They need a reliable way to run tasks, collect trajectories, score results, and feed those results back into product and RL loops.

Harbor already speaks the language of agent evals:

- task definitions
- environment setup
- verifier scripts
- trajectories
- repeated rollouts
- RL reward outputs

SolRL does not replace Harbor.

SolRL makes Harbor jobs economically and cryptographically verifiable.

## Slide 6: The Solution

SolRL is a Solana settlement and AWS Nitro execution layer for Harbor.

The product surface:

```text
AI builder
  creates Harbor task
  funds bounty
  waits for verified result

Operator
  stakes SolRL token
  receives lease
  runs AWS Nitro enclave
  submits signed claim
  gets paid if proof checks out

SolRL registry
  verifies claim
  checks PCR policy
  prevents replay
  releases escrow
  slashes bad operators
```

The user outcome is simple: more eval throughput without trusting random operators.

## Slide 7: Who Uses It

### AI agent teams

Teams building coding agents, browser agents, research agents, and internal automation agents.

They need reliable evals for every model, prompt, tool, and environment change.

### AI labs and RL teams

Teams running RLVR-style training loops.

They need reward signals that are not silently corrupted by flaky or dishonest compute.

### Benchmark maintainers

People running Terminal-Bench, SWE-Bench style tasks, private eval suites, or customer-specific benchmarks.

They need repeatable execution, artifact integrity, and clean provenance.

### Web3 AI protocols

Protocols that pay for off-chain AI work.

They need a proof that work happened before tokens move.

### Compute operators

People or companies with AWS capacity and operational skill.

They need a market where good execution earns money and bad execution loses stake.

## Slide 8: The Buyer Pain

The first buyer is not buying "TEE containers."

They are buying confidence that an eval result is real.

Their current pain:

- eval infra is brittle
- model changes need constant regression testing
- RL reward pipelines are expensive
- outsourced compute is hard to trust
- benchmark results are easy to game
- private evals need auditability

SolRL should sell the boring sentence:

```text
Pay only for evals that produce a valid proof.
```

That is a real job to be done.

## Slide 9: Potential Market

Do not start with a giant fake TAM slide. This market is early.

Start with the wedge:

```text
agent evals + RL reward rollouts + verifiable off-chain compute
```

The market expands in layers:

1. Agent teams running CI-style evals for every release.
2. RL teams paying for large batches of verified rollouts.
3. Benchmark platforms that want trusted third-party execution.
4. Web3 AI networks that need proof-of-work-style settlement without pretending GPUs can self-report honestly.
5. Enterprises that want audit logs for high-stakes agent behavior.

The first useful market is not "all cloud compute."

It is the subset of compute where the result moves money, updates a model, or affects trust.

That subset is small enough to win and valuable enough to matter.

## Slide 10: Why Solana

Solana is a good settlement layer for this because the protocol needs cheap, frequent state transitions.

Each job may need:

- job creation
- escrow funding
- operator registration
- lease creation
- claim settlement
- replay receipt
- slashing claim
- reputation or performance updates later

These are not once-a-month governance events.

They are high-frequency marketplace operations.

Solana is also a natural fit for Token-2022, transfer hooks, and fast escrow settlement.

## Slide 11: The Token

SolRL needs a token because the protocol needs more than payment.

It needs a control plane.

The token does four jobs:

1. **Escrow:** buyers fund Harbor eval jobs before work starts.
2. **Staking:** operators lock tokens before they can receive leases.
3. **Settlement:** verified claims release escrow to operators.
4. **Slashing:** forged, replayed, stale, or policy-breaking claims can burn or move operator stake.

No tokenomics needed here.

The important part is utility inside the execution loop.

## Slide 12: How The Token Affects Nitro Containers

The token does not run inside the enclave.

The enclave produces attestable facts. The chain moves tokens.

The binding looks like this:

```text
Job escrow
  token amount locked by buyer
        |
        v
Lease
  assigned to staked operator
        |
        v
Nitro worker
  runs Harbor eval
  extends PCR16 with job/operator/task context
  returns attestation
        |
        v
Verifier
  checks AWS Nitro proof
  signs ClaimV1
        |
        v
Solana registry
  verifies ClaimV1
  checks job + lease + PCR16 + nonce
  transfers escrow to operator
```

Implementation detail that matters: the Nitro worker does not invent a separate PCR16 meaning. It receives
`pcr16_user_data = sha384(Pcr16Components)`, extends PCR16 once, and the registry signs/checks the resulting locked PCR16.
Same input, same proof, same payout path. This is the whole game.

The token changes operator behavior because bad execution has a cost.

Without stake, a fake operator just disappears.

With stake, a fake operator loses money.

## Slide 13: Token Flow For Users

### Buyer flow

```text
1. Buyer creates job with Harbor task hash, image policy, timeout, reward amount.
2. Buyer deposits SolRL tokens into job escrow.
3. Protocol assigns or accepts a staked operator lease.
4. Buyer receives verified result and artifacts.
5. If the claim verifies, escrow pays the operator.
6. If the claim fails, escrow stays locked, refunds, or moves through dispute rules.
```

Buyer experience:

```text
I pay for verified eval output, not promises.
```

### Operator flow

```text
1. Operator stakes SolRL tokens.
2. Operator registers payout and stake token accounts.
3. Operator accepts a lease.
4. Operator launches AWS Nitro parent and enclave.
5. Operator submits verifier-signed ClaimV1.
6. Registry releases payment or rejects/slashes.
```

Operator experience:

```text
I monetize reliable Nitro execution.
```

## Slide 14: What Is Implemented Now

The repo already has the skeleton that matters.

Implemented:

- Docker-first local workflow.
- Mock Harbor-style worker, mock attestation, mock verifier, and mock hook.
- Canonical `ClaimV1` schema shared across Rust and Python.
- Canonical `SlashClaimV1` schema.
- PCR16 component hashing shared across Rust and Python.
- Anchor registry program that compiles.
- Operator registration with minimum stake checks.
- Job and lease accounts.
- Claim receipt and nonce receipt replay protection.
- Verifier policy and image policy accounts.
- Token-2022 `transfer_checked` CPI for payouts.
- Token-2022 transfer hook guard shape.
- Slashing path that moves stake to treasury.
- Stake withdrawal guard rails.
- First-class local MVP command: `python -m solrl_core.cli local-mock`.
- Long-running mock verifier service: `python -m solrl_core.verifier_service`.
- Harbor import path: `solrl_harbor.nitro_environment:NitroEnvironment`.
- Docker lints for AWS safety, schema parity, Token-2022 wiring, and no Docker-in-Docker.
- Real AWS Nitro smoke runner that launches tagged Nitro-enabled EC2 and boots a Nix-built EIF path.

This is not just a pitch deck.

There is code.

## Slide 15: What Is Not Done Yet

The production network is not done.

Not done:

- Production Harbor-over-Nitro execution.
- Production Harbor Nitro EIF.
- Persistent verifier enclave service.
- Full Token-2022 local-validator transaction proving hook invocation end-to-end.
- Published Anchor IDL.
- Operator marketplace scheduler.
- Buyer-facing job submission UI/API.
- Reliable real AWS result return channel beyond EC2 console output.
- Token mint deployment and operational mint authority policy.
- Reputation routing.
- Dispute/refund policy.

This is fine for the current stage.

The MVP should prove the hard technical claim first:

```text
Harbor eval result -> Nitro proof -> verifier signature -> Solana token settlement
```

## Slide 16: Implementation Plan For The Tokenized Network

### Phase 1: Make the token real on devnet

Deliverables:

- Create Token-2022 mint.
- Configure transfer hook to point at `solrl-registry`.
- Create treasury token account.
- Create buyer escrow token accounts.
- Create operator stake vault token accounts.
- Publish a minimal setup script that runs inside Docker.

Success condition:

```text
devnet token mint exists and registry config points at it
```

### Phase 2: Prove end-to-end Token-2022 settlement

Deliverables:

- Add local-validator test for Token-2022 hook invocation.
- Prove `settle_claim` transfers escrow to operator payout.
- Prove direct transfer without `TransferGuard` fails.
- Prove replayed claim fails.
- Prove slash claim transfers stake to treasury.

Success condition:

```text
one test creates mint, funds escrow, settles claim, verifies balances
```

### Phase 3: Make Harbor run inside the enclave path

Deliverables:

- Implement Harbor environment plugin.
- Package minimal Harbor worker into EIF.
- Bind Harbor task hash, verifier script hash, artifact policy, operator, payout token account, and nonce into PCR16.
- Emit `ClaimV1` from the worker flow.

Success condition:

```text
real Harbor task produces a verifier-signed claim
```

### Phase 4: Make AWS Nitro smoke reliable

Deliverables:

- Keep no-SSH, no-inbound-SG, exact-tag cleanup.
- Keep EC2 rebuild of the EIF from a public git ref.
- Replace console-output-only completion with a user-approved scoped return channel.
- Keep all AWS actions in the main runner, not sidecar scripts.

Success condition:

```text
AWS smoke can run repeatedly without orphaned resources or missing final status
```

### Phase 5: Launch the first useful market

Deliverables:

- Job API.
- Operator CLI.
- Buyer CLI.
- Devnet deployment docs.
- One real eval suite.
- Dashboard showing jobs, leases, claims, payouts, and slashes.

Success condition:

```text
an external user funds a job and an external operator gets paid for a verified eval
```

## Slide 17: Why This Can Win

Most infra projects start too broad.

SolRL starts with a painful, narrow use case:

```text
verified Harbor evals for AI agents
```

That focus matters.

General TEE compute is a giant surface area. Every workload wants different networking, storage, secrets, scheduling, and billing.

Agent evals are constrained enough to ship:

- known task format
- known verifier script
- known reward output
- known artifact bundle
- known settlement action

Narrow beats vague.

## Slide 18: The Demo That Matters

The demo should not be a dashboard pretending to be infrastructure.

The demo should show:

```text
1. Create Harbor task.
2. Fund Solana escrow with SolRL token.
3. Assign staked operator.
4. Launch Nitro enclave.
5. Run eval.
6. Show attestation/PCR16.
7. Verifier signs ClaimV1.
8. Registry settles escrow to operator.
9. Replay fails.
10. Bad claim slashes or rejects.
```

That demo tells the truth.

It shows the thing people will pay for.

## Slide 19: The Business

The business is a verified eval marketplace.

Revenue paths later:

- protocol fee on settled jobs
- enterprise hosted coordinator
- private eval network for labs
- operator tooling
- premium verifier policy registry
- audit and compliance exports

But the first business question is simpler:

```text
Can we make one AI team trust evals from one external operator?
```

If yes, scale follows.

If no, no token design saves it.

## Slide 20: The Ask

Build the smallest real loop:

```text
one Harbor task
one Nitro worker
one verifier
one Token-2022 mint
one escrow
one staked operator
one verified payout
one replay failure
one slash path
```

Then put it in front of agent teams.

The feedback loop is the company.
