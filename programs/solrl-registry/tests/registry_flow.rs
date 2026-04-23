use anchor_lang::{AccountDeserialize, AnchorSerialize};
use solana_program::{account_info::AccountInfo, entrypoint::ProgramResult};
use solana_program_test::{processor, BanksClientError, ProgramTest};
use solana_sdk::{
    ed25519_instruction::new_ed25519_instruction,
    instruction::{AccountMeta, Instruction},
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    system_instruction, system_program,
    transaction::Transaction,
};
use solrl_claim::{claim_message, pcr16_digest, ClaimV1, Pcr16Components, CLAIM_PROTOCOL_VERSION};
use solrl_registry::{
    ClaimReceipt, CreateJobArgs, CreateLeaseArgs, ImagePolicy, InitializeConfigArgs, Job, Lease,
    NonceReceipt, Operator, RegisterImagePolicyArgs, RegisterOperatorArgs, RegisterVerifierArgs,
    RegisterVerifierPolicyArgs, Verifier, VerifierPolicy,
};
use spl_token_2022::{
    extension::{ExtensionType, StateWithExtensions},
    instruction as token_instruction,
    state::{Account as Token2022Account, Mint},
};
use std::{error::Error, io};

const CLAIM_RECEIPT_STATUS_PAID: u8 = 2;
const JOB_STATUS_SETTLED: u8 = 3;
const LEASE_STATUS_SETTLED: u8 = 2;

fn hash32(byte: u8) -> [u8; 32] {
    [byte; 32]
}

fn hash48(byte: u8) -> [u8; 48] {
    [byte; 48]
}

fn family(byte: u8) -> [u8; 16] {
    [byte; 16]
}

fn pubkey(byte: u8) -> Pubkey {
    Pubkey::new_from_array([byte; 32])
}

fn discriminator(name: &str) -> [u8; 8] {
    let digest = solana_sdk::hash::hash(format!("global:{name}").as_bytes()).to_bytes();
    let mut out = [0u8; 8];
    out.copy_from_slice(&digest[..8]);
    out
}

fn instruction_data(
    name: &str,
    write_args: impl FnOnce(&mut Vec<u8>) -> io::Result<()>,
) -> io::Result<Vec<u8>> {
    let mut data = discriminator(name).to_vec();
    write_args(&mut data)?;
    Ok(data)
}

fn pda(seeds: &[&[u8]]) -> Pubkey {
    Pubkey::find_program_address(seeds, &solrl_registry::ID).0
}

fn process_instruction<'slice, 'info>(
    program_id: &Pubkey,
    accounts: &'slice [AccountInfo<'info>],
    instruction_data: &[u8],
) -> ProgramResult {
    // ProgramTest expects Solana's fully generic processor signature. Anchor's generated
    // entrypoint ties the account slice lifetime to AccountInfo's inner lifetime, so the
    // test adapter has to retie them for the duration of this synchronous call.
    // SAFETY: ProgramTest calls this processor synchronously and does not retain the
    // retied account slice. Anchor's generated entrypoint wants the slice lifetime to
    // match AccountInfo's inner lifetime, while Solana's test processor keeps them
    // separate.
    let accounts: &'info [AccountInfo<'info>] = unsafe { std::mem::transmute(accounts) };
    solrl_registry::entry(program_id, accounts, instruction_data)
}

fn token2022_process_instruction(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    instruction_data: &[u8],
) -> ProgramResult {
    spl_token_2022::processor::Processor::process(program_id, accounts, instruction_data)
}

fn registry_program_test() -> ProgramTest {
    let mut program_test = ProgramTest::new(
        "solrl_registry",
        solrl_registry::ID,
        processor!(process_instruction),
    );
    program_test.add_program(
        "spl_token_2022",
        spl_token_2022::ID,
        processor!(token2022_process_instruction),
    );
    program_test
}

async fn send(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    signers: &[&Keypair],
    ix: Instruction,
) -> Result<(), BanksClientError> {
    let recent_blockhash = banks_client.get_latest_blockhash().await?;
    let mut all_signers = Vec::with_capacity(signers.len() + 1);
    all_signers.push(payer);
    all_signers.extend_from_slice(signers);
    let tx = Transaction::new_signed_with_payer(
        &[ix],
        Some(&payer.pubkey()),
        &all_signers,
        recent_blockhash,
    );
    banks_client.process_transaction(tx).await
}

async fn send_many(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    signers: &[&Keypair],
    instructions: Vec<Instruction>,
) -> Result<(), BanksClientError> {
    let recent_blockhash = banks_client.get_latest_blockhash().await?;
    let mut all_signers = Vec::with_capacity(signers.len() + 1);
    all_signers.push(payer);
    all_signers.extend_from_slice(signers);
    let tx = Transaction::new_signed_with_payer(
        &instructions,
        Some(&payer.pubkey()),
        &all_signers,
        recent_blockhash,
    );
    banks_client.process_transaction(tx).await
}

struct RegistryFixture {
    config: Pubkey,
    verifier_policy: Pubkey,
    verifier_id: [u8; 32],
    verifier: Pubkey,
    verifier_ed25519_pubkey: [u8; 32],
    image_policy: Pubkey,
    operator: Pubkey,
    stake_authority: Pubkey,
    job: Pubkey,
    escrow_authority: Pubkey,
    lease: Pubkey,
    lease_id: [u8; 32],
    operator_owner: Keypair,
    wrong_operator_owner: Keypair,
    token_mint_keypair: Keypair,
    escrow_token_account_keypair: Keypair,
    payout_token_account_keypair: Keypair,
    token_mint: Pubkey,
    payout_token_account: Pubkey,
    nonce: [u8; 32],
    create_job_args: CreateJobArgs,
    create_lease_args: CreateLeaseArgs,
}

impl RegistryFixture {
    fn new() -> Self {
        let config = pda(&[b"config"]);
        let verifier_policy_id = hash32(10);
        let verifier_id = hash32(40);
        let image_policy_id = hash32(11);
        let operator_owner = Keypair::new();
        let wrong_operator_owner = Keypair::new();
        let token_mint_keypair = Keypair::new();
        let escrow_token_account_keypair = Keypair::new();
        let payout_token_account_keypair = Keypair::new();
        let operator = pda(&[
            b"operator",
            config.as_ref(),
            operator_owner.pubkey().as_ref(),
        ]);
        let stake_authority = pda(&[b"stake_authority", operator.as_ref()]);
        let job_id = hash32(12);
        let job = pda(&[b"job", config.as_ref(), job_id.as_ref()]);
        let escrow_authority = pda(&[b"escrow_authority", job.as_ref()]);
        let lease_id = hash32(13);
        let lease = pda(&[b"lease", job.as_ref(), lease_id.as_ref()]);
        let token_mint = token_mint_keypair.pubkey();
        let payout_token_account = payout_token_account_keypair.pubkey();
        let escrow_token_account = escrow_token_account_keypair.pubkey();
        let nonce = hash32(24);

        let create_job_args = CreateJobArgs {
            escrow_token_account,
            amount: 100,
            verifier_policy_id,
            image_policy_id,
            task_hash: hash32(30),
            task_toml_hash: hash32(31),
            instruction_hash: hash32(32),
            test_hash: hash32(33),
            reward_script_hash: hash32(34),
            harbor_environment_hash: hash32(35),
            artifact_policy_hash: hash32(36),
            network_policy_hash: hash32(37),
            resource_class_hash: hash32(38),
            timeout_seconds: 900,
            expires_at: 4_102_444_800,
        };
        let create_lease_args = CreateLeaseArgs {
            nonce,
            expected_worker_public_key_hash: hash32(39),
            expires_at: 4_102_444_000,
        };

        Self {
            config,
            verifier_policy: pda(&[
                b"verifier_policy",
                config.as_ref(),
                verifier_policy_id.as_ref(),
            ]),
            verifier_id,
            verifier: pda(&[b"verifier", config.as_ref(), verifier_id.as_ref()]),
            verifier_ed25519_pubkey: hash32(41),
            image_policy: pda(&[b"image_policy", config.as_ref(), image_policy_id.as_ref()]),
            operator,
            stake_authority,
            job,
            escrow_authority,
            lease,
            lease_id,
            operator_owner,
            wrong_operator_owner,
            token_mint_keypair,
            escrow_token_account_keypair,
            payout_token_account_keypair,
            token_mint,
            payout_token_account,
            nonce,
            create_job_args,
            create_lease_args,
        }
    }

    fn expected_pcr16(&self) -> io::Result<[u8; 48]> {
        pcr16_digest(&Pcr16Components {
            job_account: self.job,
            lease_account: self.lease,
            nonce: self.nonce,
            task_hash: self.create_job_args.task_hash,
            task_toml_hash: self.create_job_args.task_toml_hash,
            instruction_hash: self.create_job_args.instruction_hash,
            test_hash: self.create_job_args.test_hash,
            reward_script_hash: self.create_job_args.reward_script_hash,
            harbor_environment_hash: self.create_job_args.harbor_environment_hash,
            resource_class_hash: self.create_job_args.resource_class_hash,
            timeout_seconds: self.create_job_args.timeout_seconds,
            network_policy_hash: self.create_job_args.network_policy_hash,
            operator_account: self.operator,
            payout_token_account: self.payout_token_account,
            token_mint: self.token_mint,
            artifact_policy_hash: self.create_job_args.artifact_policy_hash,
            protocol_version: CLAIM_PROTOCOL_VERSION,
        })
    }
}

fn extra_account_meta_list(mint: Pubkey) -> Pubkey {
    pda(&[b"extra-account-metas", mint.as_ref()])
}

fn transfer_guard(
    source: Pubkey,
    mint: Pubkey,
    destination: Pubkey,
    owner: Pubkey,
    amount: u64,
) -> Pubkey {
    pda(&[
        b"transfer_guard",
        source.as_ref(),
        mint.as_ref(),
        destination.as_ref(),
        owner.as_ref(),
        &amount.to_le_bytes(),
    ])
}

async fn create_lease(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    fixture: &RegistryFixture,
) -> Result<(), BanksClientError> {
    send(
        banks_client,
        payer,
        &[&fixture.operator_owner],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.job, false),
                AccountMeta::new(fixture.operator, false),
                AccountMeta::new(fixture.lease, false),
                AccountMeta::new_readonly(fixture.operator_owner.pubkey(), true),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("create_lease", |data| {
                fixture.lease_id.serialize(data)?;
                fixture.create_lease_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await
}

async fn withdraw_stake(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    fixture: &RegistryFixture,
    amount: u64,
    destination_token_account: Pubkey,
    deactivate: bool,
) -> Result<(), BanksClientError> {
    send(
        banks_client,
        payer,
        &[&fixture.operator_owner],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.operator, false),
                AccountMeta::new(pubkey(22), false),
                AccountMeta::new(destination_token_account, false),
                AccountMeta::new_readonly(fixture.token_mint, false),
                AccountMeta::new_readonly(fixture.stake_authority, false),
                AccountMeta::new_readonly(extra_account_meta_list(fixture.token_mint), false),
                AccountMeta::new(
                    transfer_guard(
                        pubkey(22),
                        fixture.token_mint,
                        destination_token_account,
                        fixture.stake_authority,
                        amount,
                    ),
                    false,
                ),
                AccountMeta::new_readonly(spl_token_2022::ID, false),
                AccountMeta::new_readonly(solana_program::sysvar::instructions::ID, false),
                AccountMeta::new(fixture.operator_owner.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("withdraw_stake", |data| {
                amount.serialize(data)?;
                deactivate.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await
}

async fn create_token2022_mint_and_accounts(
    context: &mut solana_program_test::ProgramTestContext,
    fixture: &RegistryFixture,
) -> Result<(), Box<dyn Error>> {
    let rent = context.banks_client.get_rent().await?;
    let mint_len = ExtensionType::try_calculate_account_len::<Mint>(&[])?;
    let token_account_len = ExtensionType::try_calculate_account_len::<Token2022Account>(&[])?;

    send_many(
        &mut context.banks_client,
        &context.payer,
        &[&fixture.token_mint_keypair],
        vec![
            system_instruction::create_account(
                &context.payer.pubkey(),
                &fixture.token_mint,
                rent.minimum_balance(mint_len),
                mint_len as u64,
                &spl_token_2022::ID,
            ),
            token_instruction::initialize_mint2(
                &spl_token_2022::ID,
                &fixture.token_mint,
                &context.payer.pubkey(),
                None,
                6,
            )?,
        ],
    )
    .await?;

    send_many(
        &mut context.banks_client,
        &context.payer,
        &[
            &fixture.escrow_token_account_keypair,
            &fixture.payout_token_account_keypair,
        ],
        vec![
            system_instruction::create_account(
                &context.payer.pubkey(),
                &fixture.create_job_args.escrow_token_account,
                rent.minimum_balance(token_account_len),
                token_account_len as u64,
                &spl_token_2022::ID,
            ),
            token_instruction::initialize_account3(
                &spl_token_2022::ID,
                &fixture.create_job_args.escrow_token_account,
                &fixture.token_mint,
                &fixture.escrow_authority,
            )?,
            system_instruction::create_account(
                &context.payer.pubkey(),
                &fixture.payout_token_account,
                rent.minimum_balance(token_account_len),
                token_account_len as u64,
                &spl_token_2022::ID,
            ),
            token_instruction::initialize_account3(
                &spl_token_2022::ID,
                &fixture.payout_token_account,
                &fixture.token_mint,
                &fixture.operator_owner.pubkey(),
            )?,
        ],
    )
    .await?;

    send(
        &mut context.banks_client,
        &context.payer,
        &[],
        token_instruction::mint_to(
            &spl_token_2022::ID,
            &fixture.token_mint,
            &fixture.create_job_args.escrow_token_account,
            &context.payer.pubkey(),
            &[],
            fixture.create_job_args.amount,
        )?,
    )
    .await?;

    Ok(())
}

async fn initialize_extra_account_meta_list(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    fixture: &RegistryFixture,
) -> Result<(), BanksClientError> {
    send(
        banks_client,
        payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(extra_account_meta_list(fixture.token_mint), false),
                AccountMeta::new_readonly(fixture.token_mint, false),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("initialize_extra_account_meta_list", |_| Ok(()))?,
        },
    )
    .await
}

async fn register_verifier(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    fixture: &RegistryFixture,
    ed25519_pubkey: [u8; 32],
) -> Result<(), BanksClientError> {
    send(
        banks_client,
        payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new_readonly(fixture.verifier_policy, false),
                AccountMeta::new(fixture.verifier, false),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("register_verifier", |data| {
                fixture.verifier_id.serialize(data)?;
                RegisterVerifierArgs {
                    ed25519_pubkey,
                    family: family(3),
                    version: 1,
                    policy_id: fixture.create_job_args.verifier_policy_id,
                    active: true,
                }
                .serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await
}

fn verifier_keypair() -> Result<ed25519_dalek::Keypair, ed25519_dalek::SignatureError> {
    let secret = ed25519_dalek::SecretKey::from_bytes(&[42u8; 32])?;
    let public = ed25519_dalek::PublicKey::from(&secret);
    Ok(ed25519_dalek::Keypair { secret, public })
}

fn settlement_claim(fixture: &RegistryFixture, claim_receipt: Pubkey) -> io::Result<ClaimV1> {
    Ok(ClaimV1 {
        cluster_hash: hash32(1),
        program_id: solrl_registry::ID,
        token_mint: fixture.token_mint,
        hook_program_id: solrl_registry::ID,
        job_account: fixture.job,
        lease_account: fixture.lease,
        claim_receipt_account: claim_receipt,
        operator_account: fixture.operator,
        payout_token_account: fixture.payout_token_account,
        amount: fixture.create_job_args.amount,
        resource_class_hash: fixture.create_job_args.resource_class_hash,
        verifier_policy_id: fixture.create_job_args.verifier_policy_id,
        image_policy_id: fixture.create_job_args.image_policy_id,
        worker_public_key_hash: fixture.create_lease_args.expected_worker_public_key_hash,
        task_hash: fixture.create_job_args.task_hash,
        reward_script_hash: fixture.create_job_args.reward_script_hash,
        harbor_environment_hash: fixture.create_job_args.harbor_environment_hash,
        artifact_policy_hash: fixture.create_job_args.artifact_policy_hash,
        network_policy_hash: fixture.create_job_args.network_policy_hash,
        attestation_document_hash: hash32(70),
        trajectory_hash: hash32(71),
        pcr16: fixture.expected_pcr16()?,
        reward_value: 1,
        lease_expiry_unix: fixture.create_lease_args.expires_at,
        claim_expiry_unix: fixture.create_lease_args.expires_at,
        nonce: fixture.nonce,
        protocol_version: CLAIM_PROTOCOL_VERSION,
    })
}

async fn token_amount(
    banks_client: &mut solana_program_test::BanksClient,
    token_account: Pubkey,
) -> Result<u64, Box<dyn Error>> {
    let account = banks_client
        .get_account(token_account)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "token account missing"))?;
    let state = StateWithExtensions::<Token2022Account>::unpack(&account.data)?;
    Ok(state.base.amount)
}

async fn bootstrap_registry(
    banks_client: &mut solana_program_test::BanksClient,
    payer: &Keypair,
    fixture: &RegistryFixture,
) -> Result<(), Box<dyn Error>> {
    let init_args = InitializeConfigArgs {
        cluster_hash: hash32(1),
        token_mint: fixture.token_mint,
        treasury_token_account: pubkey(2),
        token_decimals: 6,
        min_operator_stake: 1_000,
    };
    send(
        banks_client,
        payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new(fixture.config, false),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("initialize_config", |data| {
                init_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

    let verifier_policy_args = RegisterVerifierPolicyArgs {
        family: family(3),
        min_version: 1,
        active: true,
    };
    send(
        banks_client,
        payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.verifier_policy, false),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("register_verifier_policy", |data| {
                fixture.create_job_args.verifier_policy_id.serialize(data)?;
                verifier_policy_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

    let image_policy_args = RegisterImagePolicyArgs {
        family: family(4),
        version: 1,
        pcr0: hash48(5),
        pcr1: hash48(6),
        pcr2: hash48(7),
        active: true,
    };
    send(
        banks_client,
        payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.image_policy, false),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("register_image_policy", |data| {
                fixture.create_job_args.image_policy_id.serialize(data)?;
                image_policy_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

    let register_operator_args = RegisterOperatorArgs {
        payout_token_account: fixture.payout_token_account,
        stake_token_account: pubkey(22),
        stake_amount: 1_000,
    };
    send(
        banks_client,
        payer,
        &[],
        system_instruction::transfer(&payer.pubkey(), &fixture.operator_owner.pubkey(), 5_000_000),
    )
    .await?;
    send(
        banks_client,
        payer,
        &[&fixture.operator_owner],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.operator, false),
                AccountMeta::new_readonly(fixture.stake_authority, false),
                AccountMeta::new(fixture.operator_owner.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("register_operator", |data| {
                register_operator_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

    send(
        banks_client,
        payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new(fixture.config, false),
                AccountMeta::new_readonly(fixture.verifier_policy, false),
                AccountMeta::new_readonly(fixture.image_policy, false),
                AccountMeta::new(fixture.job, false),
                AccountMeta::new_readonly(fixture.escrow_authority, false),
                AccountMeta::new(payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("create_job", |data| {
                hash32(12).serialize(data)?;
                fixture.create_job_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

    Ok(())
}

#[tokio::test]
async fn create_lease_requires_operator_owner_and_computes_pcr16() -> Result<(), Box<dyn Error>> {
    let program_test = ProgramTest::new(
        "solrl_registry",
        solrl_registry::ID,
        processor!(process_instruction),
    );
    let fixture = RegistryFixture::new();
    let mut context = program_test.start_with_context().await;

    bootstrap_registry(&mut context.banks_client, &context.payer, &fixture).await?;

    let wrong_lease = pda(&[b"lease", fixture.job.as_ref(), hash32(99).as_ref()]);
    let wrong_result = send(
        &mut context.banks_client,
        &context.payer,
        &[&fixture.wrong_operator_owner],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.job, false),
                AccountMeta::new(fixture.operator, false),
                AccountMeta::new(wrong_lease, false),
                AccountMeta::new_readonly(fixture.wrong_operator_owner.pubkey(), true),
                AccountMeta::new(context.payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("create_lease", |data| {
                hash32(99).serialize(data)?;
                fixture.create_lease_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await;
    assert!(wrong_result.is_err());

    create_lease(&mut context.banks_client, &context.payer, &fixture).await?;

    let lease_account = context
        .banks_client
        .get_account(fixture.lease)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "lease account missing"))?;
    let lease = solrl_registry::Lease::try_deserialize(&mut lease_account.data.as_slice())?;
    assert_eq!(lease.expected_pcr16, fixture.expected_pcr16()?);

    let operator_account = context
        .banks_client
        .get_account(fixture.operator)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "operator account missing"))?;
    let operator = Operator::try_deserialize(&mut operator_account.data.as_slice())?;
    assert_eq!(operator.active_lease_count, 1);

    let image_policy_account = context
        .banks_client
        .get_account(fixture.image_policy)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "image policy missing"))?;
    let image_policy = ImagePolicy::try_deserialize(&mut image_policy_account.data.as_slice())?;
    assert_eq!(image_policy.pcr0, hash48(5));

    let verifier_policy_account = context
        .banks_client
        .get_account(fixture.verifier_policy)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "verifier policy missing"))?;
    let verifier_policy =
        VerifierPolicy::try_deserialize(&mut verifier_policy_account.data.as_slice())?;
    assert!(verifier_policy.active);

    let withdraw_result = withdraw_stake(
        &mut context.banks_client,
        &context.payer,
        &fixture,
        1,
        pubkey(44),
        false,
    )
    .await;
    assert!(withdraw_result.is_err());
    let operator_account = context
        .banks_client
        .get_account(fixture.operator)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "operator account missing"))?;
    let operator = Operator::try_deserialize(&mut operator_account.data.as_slice())?;
    assert_eq!(operator.stake_amount, 1_000);
    Ok(())
}

#[tokio::test]
async fn register_verifier_requires_matching_active_policy() -> Result<(), Box<dyn Error>> {
    let program_test = ProgramTest::new(
        "solrl_registry",
        solrl_registry::ID,
        processor!(process_instruction),
    );
    let fixture = RegistryFixture::new();
    let mut context = program_test.start_with_context().await;

    bootstrap_registry(&mut context.banks_client, &context.payer, &fixture).await?;

    let too_old_verifier_id = hash32(98);
    let too_old_result = send(
        &mut context.banks_client,
        &context.payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new_readonly(fixture.verifier_policy, false),
                AccountMeta::new(
                    pda(&[
                        b"verifier",
                        fixture.config.as_ref(),
                        too_old_verifier_id.as_ref(),
                    ]),
                    false,
                ),
                AccountMeta::new(context.payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("register_verifier", |data| {
                too_old_verifier_id.serialize(data)?;
                RegisterVerifierArgs {
                    ed25519_pubkey: fixture.verifier_ed25519_pubkey,
                    family: family(3),
                    version: 0,
                    policy_id: fixture.create_job_args.verifier_policy_id,
                    active: true,
                }
                .serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await;
    assert!(too_old_result.is_err());

    send(
        &mut context.banks_client,
        &context.payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new_readonly(fixture.verifier_policy, false),
                AccountMeta::new(fixture.verifier, false),
                AccountMeta::new(context.payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("register_verifier", |data| {
                fixture.verifier_id.serialize(data)?;
                RegisterVerifierArgs {
                    ed25519_pubkey: fixture.verifier_ed25519_pubkey,
                    family: family(3),
                    version: 1,
                    policy_id: fixture.create_job_args.verifier_policy_id,
                    active: true,
                }
                .serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

    let verifier_account = context
        .banks_client
        .get_account(fixture.verifier)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "verifier account missing"))?;
    let verifier = Verifier::try_deserialize(&mut verifier_account.data.as_slice())?;
    assert_eq!(
        verifier.policy_id,
        fixture.create_job_args.verifier_policy_id
    );
    assert_eq!(verifier.ed25519_pubkey, fixture.verifier_ed25519_pubkey);
    assert!(verifier.active);
    Ok(())
}

#[tokio::test]
async fn settle_claim_transfers_token2022_balance_with_registry_pda_authority(
) -> Result<(), Box<dyn Error>> {
    let program_test = registry_program_test();
    let fixture = RegistryFixture::new();
    let mut context = program_test.start_with_context().await;

    create_token2022_mint_and_accounts(&mut context, &fixture).await?;
    bootstrap_registry(&mut context.banks_client, &context.payer, &fixture).await?;
    initialize_extra_account_meta_list(&mut context.banks_client, &context.payer, &fixture).await?;

    let verifier_keypair = verifier_keypair()?;
    register_verifier(
        &mut context.banks_client,
        &context.payer,
        &fixture,
        verifier_keypair.public.to_bytes(),
    )
    .await?;
    create_lease(&mut context.banks_client, &context.payer, &fixture).await?;

    let claim_receipt = pda(&[
        b"claim_receipt",
        fixture.lease.as_ref(),
        fixture.nonce.as_ref(),
    ]);
    let nonce_receipt = pda(&[b"nonce", fixture.config.as_ref(), fixture.nonce.as_ref()]);
    let claim = settlement_claim(&fixture, claim_receipt)?;
    let transfer_guard = transfer_guard(
        fixture.create_job_args.escrow_token_account,
        fixture.token_mint,
        fixture.payout_token_account,
        fixture.escrow_authority,
        claim.amount,
    );

    let escrow_before = token_amount(
        &mut context.banks_client,
        fixture.create_job_args.escrow_token_account,
    )
    .await?;
    let payout_before =
        token_amount(&mut context.banks_client, fixture.payout_token_account).await?;
    assert_eq!(escrow_before, claim.amount);
    assert_eq!(payout_before, 0);

    let signature_ix = new_ed25519_instruction(&verifier_keypair, &claim_message(&claim)?);
    let settle_ix = Instruction {
        program_id: solrl_registry::ID,
        accounts: vec![
            AccountMeta::new_readonly(fixture.config, false),
            AccountMeta::new(fixture.job, false),
            AccountMeta::new(fixture.lease, false),
            AccountMeta::new(fixture.operator, false),
            AccountMeta::new_readonly(fixture.verifier, false),
            AccountMeta::new_readonly(fixture.verifier_policy, false),
            AccountMeta::new_readonly(fixture.image_policy, false),
            AccountMeta::new(claim_receipt, false),
            AccountMeta::new(nonce_receipt, false),
            AccountMeta::new(fixture.create_job_args.escrow_token_account, false),
            AccountMeta::new(fixture.payout_token_account, false),
            AccountMeta::new_readonly(fixture.token_mint, false),
            AccountMeta::new_readonly(fixture.escrow_authority, false),
            AccountMeta::new(transfer_guard, false),
            AccountMeta::new_readonly(extra_account_meta_list(fixture.token_mint), false),
            AccountMeta::new_readonly(spl_token_2022::ID, false),
            AccountMeta::new_readonly(solana_program::sysvar::instructions::ID, false),
            AccountMeta::new(context.payer.pubkey(), true),
            AccountMeta::new_readonly(system_program::ID, false),
        ],
        data: instruction_data("settle_claim", |data| {
            claim.serialize(data)?;
            0u8.serialize(data)?;
            Ok(())
        })?,
    };

    send_many(
        &mut context.banks_client,
        &context.payer,
        &[],
        vec![signature_ix, settle_ix],
    )
    .await?;

    let escrow_after = token_amount(
        &mut context.banks_client,
        fixture.create_job_args.escrow_token_account,
    )
    .await?;
    let payout_after =
        token_amount(&mut context.banks_client, fixture.payout_token_account).await?;
    assert_eq!(escrow_after, 0);
    assert_eq!(payout_after, claim.amount);

    let receipt_account = context
        .banks_client
        .get_account(claim_receipt)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "claim receipt missing"))?;
    let receipt = ClaimReceipt::try_deserialize(&mut receipt_account.data.as_slice())?;
    assert_eq!(receipt.status, CLAIM_RECEIPT_STATUS_PAID);
    assert_eq!(receipt.amount, claim.amount);
    assert_eq!(receipt.payout_token_account, fixture.payout_token_account);

    let nonce_account = context
        .banks_client
        .get_account(nonce_receipt)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "nonce receipt missing"))?;
    let nonce = NonceReceipt::try_deserialize(&mut nonce_account.data.as_slice())?;
    assert!(nonce.consumed);

    let job_account = context
        .banks_client
        .get_account(fixture.job)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "job missing"))?;
    let job = Job::try_deserialize(&mut job_account.data.as_slice())?;
    assert_eq!(job.status, JOB_STATUS_SETTLED);

    let lease_account = context
        .banks_client
        .get_account(fixture.lease)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "lease missing"))?;
    let lease = Lease::try_deserialize(&mut lease_account.data.as_slice())?;
    assert_eq!(lease.status, LEASE_STATUS_SETTLED);

    let guard_account = context.banks_client.get_account(transfer_guard).await?;
    if let Some(account) = guard_account {
        assert_eq!(account.lamports, 0);
    }

    Ok(())
}

#[tokio::test]
async fn initialize_extra_account_meta_list_creates_hook_validation_account(
) -> Result<(), Box<dyn Error>> {
    let program_test = ProgramTest::new(
        "solrl_registry",
        solrl_registry::ID,
        processor!(process_instruction),
    );
    let fixture = RegistryFixture::new();
    let mut context = program_test.start_with_context().await;

    bootstrap_registry(&mut context.banks_client, &context.payer, &fixture).await?;

    let validation = extra_account_meta_list(fixture.token_mint);
    send(
        &mut context.banks_client,
        &context.payer,
        &[],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(validation, false),
                AccountMeta::new_readonly(fixture.token_mint, false),
                AccountMeta::new(context.payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("initialize_extra_account_meta_list", |_| Ok(()))?,
        },
    )
    .await?;

    let validation_account = context
        .banks_client
        .get_account(validation)
        .await?
        .ok_or_else(|| {
            io::Error::new(io::ErrorKind::NotFound, "extra account meta list missing")
        })?;
    assert_eq!(validation_account.owner, solrl_registry::ID);
    assert!(!validation_account.data.is_empty());
    Ok(())
}

#[tokio::test]
async fn withdraw_stake_rejects_partial_exit_below_minimum_before_token_cpi(
) -> Result<(), Box<dyn Error>> {
    let program_test = ProgramTest::new(
        "solrl_registry",
        solrl_registry::ID,
        processor!(process_instruction),
    );
    let fixture = RegistryFixture::new();
    let mut context = program_test.start_with_context().await;

    bootstrap_registry(&mut context.banks_client, &context.payer, &fixture).await?;

    let result = withdraw_stake(
        &mut context.banks_client,
        &context.payer,
        &fixture,
        1,
        pubkey(45),
        false,
    )
    .await;
    assert!(result.is_err());

    let operator_account = context
        .banks_client
        .get_account(fixture.operator)
        .await?
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "operator account missing"))?;
    let operator = Operator::try_deserialize(&mut operator_account.data.as_slice())?;
    assert_eq!(operator.stake_amount, 1_000);
    assert_eq!(operator.active_lease_count, 0);
    Ok(())
}
