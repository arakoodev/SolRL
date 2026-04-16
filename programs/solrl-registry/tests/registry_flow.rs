use anchor_lang::{AccountDeserialize, AnchorSerialize};
use solana_program::{account_info::AccountInfo, entrypoint::ProgramResult};
use solana_program_test::{processor, BanksClientError, ProgramTest};
use solana_sdk::{
    instruction::{AccountMeta, Instruction},
    pubkey::Pubkey,
    signature::{Keypair, Signer},
    system_instruction, system_program,
    transaction::Transaction,
};
use solrl_claim::{pcr16_digest, Pcr16Components, CLAIM_PROTOCOL_VERSION};
use solrl_registry::{
    CreateJobArgs, CreateLeaseArgs, ImagePolicy, InitializeConfigArgs, Operator,
    RegisterImagePolicyArgs, RegisterOperatorArgs, RegisterVerifierPolicyArgs, VerifierPolicy,
};
use std::{error::Error, io};

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
    let accounts: &'info [AccountInfo<'info>] = unsafe { std::mem::transmute(accounts) };
    solrl_registry::entry(program_id, accounts, instruction_data)
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

struct RegistryFixture {
    config: Pubkey,
    verifier_policy: Pubkey,
    image_policy: Pubkey,
    operator: Pubkey,
    stake_authority: Pubkey,
    job: Pubkey,
    escrow_authority: Pubkey,
    lease: Pubkey,
    lease_id: [u8; 32],
    operator_owner: Keypair,
    wrong_operator_owner: Keypair,
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
        let image_policy_id = hash32(11);
        let operator_owner = Keypair::new();
        let wrong_operator_owner = Keypair::new();
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
        let token_mint = pubkey(20);
        let payout_token_account = pubkey(21);
        let escrow_token_account = pubkey(23);
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
            image_policy: pda(&[b"image_policy", config.as_ref(), image_policy_id.as_ref()]),
            operator,
            stake_authority,
            job,
            escrow_authority,
            lease,
            lease_id,
            operator_owner,
            wrong_operator_owner,
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

    send(
        &mut context.banks_client,
        &context.payer,
        &[&fixture.operator_owner],
        Instruction {
            program_id: solrl_registry::ID,
            accounts: vec![
                AccountMeta::new_readonly(fixture.config, false),
                AccountMeta::new(fixture.job, false),
                AccountMeta::new(fixture.operator, false),
                AccountMeta::new(fixture.lease, false),
                AccountMeta::new_readonly(fixture.operator_owner.pubkey(), true),
                AccountMeta::new(context.payer.pubkey(), true),
                AccountMeta::new_readonly(system_program::ID, false),
            ],
            data: instruction_data("create_lease", |data| {
                fixture.lease_id.serialize(data)?;
                fixture.create_lease_args.serialize(data)?;
                Ok(())
            })?,
        },
    )
    .await?;

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
    Ok(())
}
