use anchor_lang::prelude::*;
use anchor_lang::solana_program::{
    ed25519_program,
    instruction::{AccountMeta, Instruction},
    program::invoke_signed,
    program_error::ProgramError,
    system_instruction,
    sysvar::instructions::{load_instruction_at_checked, ID as INSTRUCTIONS_ID},
};
use solrl_claim::{
    claim_hash, claim_message, slash_claim_hash, slash_claim_message, ClaimV1, SlashClaimV1,
    CLAIM_PROTOCOL_VERSION, SLASH_REASON_FORGED_CONTEXT, SLASH_REASON_LEASE_ABUSE,
    SLASH_REASON_MALICIOUS_DUPLICATE, SLASH_REASON_REPLAY, SLASH_REASON_WRONG_JOB,
    SLASH_REASON_WRONG_POLICY,
};
use spl_tlv_account_resolution::{account::ExtraAccountMeta, state::ExtraAccountMetaList};
use spl_token_2022::{
    extension::{transfer_hook::TransferHookAccount, BaseStateWithExtensions, StateWithExtensions},
    state::Account as Token2022Account,
};
use spl_transfer_hook_interface::instruction::{ExecuteInstruction, TransferHookInstruction};

declare_id!("GUo5ybeouLfzNAG98F99xeARFYsGBosu7qrp8ddy2k2u");

const OPERATOR_STATUS_ACTIVE: u8 = 1;
const JOB_STATUS_OPEN: u8 = 1;
const JOB_STATUS_LEASED: u8 = 2;
const JOB_STATUS_SETTLED: u8 = 3;
const LEASE_STATUS_ACTIVE: u8 = 1;
const LEASE_STATUS_SETTLED: u8 = 2;
const LEASE_STATUS_SLASHED: u8 = 3;
const RECEIPT_STATUS_PENDING: u8 = 1;
const RECEIPT_STATUS_CONSUMED: u8 = 2;

#[program]
pub mod solrl_registry {
    use super::*;

    pub fn fallback<'info>(
        program_id: &Pubkey,
        accounts: &'info [AccountInfo<'info>],
        data: &[u8],
    ) -> Result<()> {
        let instruction = TransferHookInstruction::unpack(data)?;
        match instruction {
            TransferHookInstruction::Execute { amount } => {
                let amount_bytes = amount.to_le_bytes();
                __private::__global::transfer_hook(program_id, accounts, &amount_bytes)
            }
            _ => Err(ProgramError::InvalidInstructionData.into()),
        }
    }

    pub fn initialize_config(
        ctx: Context<InitializeConfig>,
        args: InitializeConfigArgs,
    ) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.admin = ctx.accounts.admin.key();
        config.cluster_hash = args.cluster_hash;
        config.token_mint = args.token_mint;
        config.treasury_token_account = args.treasury_token_account;
        config.token_decimals = args.token_decimals;
        config.min_operator_stake = args.min_operator_stake;
        config.paused = false;
        config.job_count = 0;
        config.bump = ctx.bumps.config;

        emit!(ConfigInitialized {
            config: config.key(),
            admin: config.admin,
            token_mint: config.token_mint,
            cluster_hash: config.cluster_hash,
        });

        Ok(())
    }

    pub fn set_pause(ctx: Context<AdminConfig>, paused: bool) -> Result<()> {
        ctx.accounts.config.require_admin(&ctx.accounts.admin)?;
        ctx.accounts.config.paused = paused;
        emit!(ConfigPauseSet {
            config: ctx.accounts.config.key(),
            paused,
        });
        Ok(())
    }

    pub fn initialize_extra_account_meta_list(
        ctx: Context<InitializeExtraAccountMetaList>,
    ) -> Result<()> {
        ctx.accounts.config.require_admin(&ctx.accounts.admin)?;

        let account_metas = vec![ExtraAccountMeta::new_with_pubkey(
            &ctx.accounts.config.key(),
            false,
            false,
        )?];
        let account_size = ExtraAccountMetaList::size_of(account_metas.len())?;
        let lamports = Rent::get()?.minimum_balance(account_size);
        let mint_key = ctx.accounts.mint.key();
        let signer_seeds: &[&[&[u8]]] = &[&[
            b"extra-account-metas",
            mint_key.as_ref(),
            &[ctx.bumps.extra_account_meta_list],
        ]];

        invoke_signed(
            &system_instruction::create_account(
                &ctx.accounts.admin.key(),
                &ctx.accounts.extra_account_meta_list.key(),
                lamports,
                account_size as u64,
                ctx.program_id,
            ),
            &[
                ctx.accounts.admin.to_account_info(),
                ctx.accounts.extra_account_meta_list.to_account_info(),
                ctx.accounts.system_program.to_account_info(),
            ],
            signer_seeds,
        )?;

        let mut data = ctx.accounts.extra_account_meta_list.try_borrow_mut_data()?;
        ExtraAccountMetaList::init::<ExecuteInstruction>(&mut data, &account_metas)?;

        Ok(())
    }

    pub fn register_verifier_policy(
        ctx: Context<RegisterVerifierPolicy>,
        verifier_policy_id: [u8; 32],
        args: RegisterVerifierPolicyArgs,
    ) -> Result<()> {
        ctx.accounts.config.require_admin(&ctx.accounts.admin)?;

        let policy = &mut ctx.accounts.verifier_policy;
        policy.config = ctx.accounts.config.key();
        policy.verifier_policy_id = verifier_policy_id;
        policy.family = args.family;
        policy.min_version = args.min_version;
        policy.active = args.active;
        policy.bump = ctx.bumps.verifier_policy;

        emit!(VerifierPolicyRegistered {
            verifier_policy: policy.key(),
            verifier_policy_id,
            active: policy.active,
        });

        Ok(())
    }

    pub fn register_verifier(
        ctx: Context<RegisterVerifier>,
        verifier_id: [u8; 32],
        args: RegisterVerifierArgs,
    ) -> Result<()> {
        ctx.accounts.config.require_admin(&ctx.accounts.admin)?;

        let verifier = &mut ctx.accounts.verifier;
        verifier.config = ctx.accounts.config.key();
        verifier.verifier_id = verifier_id;
        verifier.ed25519_pubkey = args.ed25519_pubkey;
        verifier.family = args.family;
        verifier.version = args.version;
        verifier.policy_id = args.policy_id;
        verifier.active = args.active;
        verifier.bump = ctx.bumps.verifier;

        emit!(VerifierRegistered {
            verifier: verifier.key(),
            verifier_id,
            active: verifier.active,
        });

        Ok(())
    }

    pub fn register_image_policy(
        ctx: Context<RegisterImagePolicy>,
        image_policy_id: [u8; 32],
        args: RegisterImagePolicyArgs,
    ) -> Result<()> {
        ctx.accounts.config.require_admin(&ctx.accounts.admin)?;

        let policy = &mut ctx.accounts.image_policy;
        policy.config = ctx.accounts.config.key();
        policy.image_policy_id = image_policy_id;
        policy.family = args.family;
        policy.version = args.version;
        policy.pcr0 = args.pcr0;
        policy.pcr1 = args.pcr1;
        policy.pcr2 = args.pcr2;
        policy.pcr16 = args.pcr16;
        policy.active = args.active;
        policy.bump = ctx.bumps.image_policy;

        emit!(ImagePolicyRegistered {
            image_policy: policy.key(),
            image_policy_id,
            active: policy.active,
        });

        Ok(())
    }

    pub fn register_operator(
        ctx: Context<RegisterOperator>,
        args: RegisterOperatorArgs,
    ) -> Result<()> {
        ctx.accounts.config.require_not_paused()?;
        require!(
            args.stake_amount >= ctx.accounts.config.min_operator_stake,
            SolrlError::InsufficientStake
        );

        let operator = &mut ctx.accounts.operator;
        operator.config = ctx.accounts.config.key();
        operator.owner = ctx.accounts.owner.key();
        operator.payout_token_account = args.payout_token_account;
        operator.stake_token_account = args.stake_token_account;
        operator.stake_authority = ctx.accounts.stake_authority.key();
        operator.stake_amount = args.stake_amount;
        operator.active_lease_count = 0;
        operator.total_slashed = 0;
        operator.slash_count = 0;
        operator.status = OPERATOR_STATUS_ACTIVE;
        operator.bump = ctx.bumps.operator;
        operator.stake_authority_bump = ctx.bumps.stake_authority;

        emit!(OperatorRegistered {
            operator: operator.key(),
            owner: operator.owner,
            stake_amount: operator.stake_amount,
        });

        Ok(())
    }

    pub fn create_job(
        ctx: Context<CreateJob>,
        job_id: [u8; 32],
        args: CreateJobArgs,
    ) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.require_not_paused()?;

        require!(args.amount > 0, SolrlError::InvalidAmount);
        require!(args.timeout_seconds > 0, SolrlError::InvalidTimeout);
        require!(
            args.expires_at > Clock::get()?.unix_timestamp,
            SolrlError::Expired
        );

        let job = &mut ctx.accounts.job;
        job.config = config.key();
        job.job_id = job_id;
        job.owner = ctx.accounts.owner.key();
        job.escrow_token_account = args.escrow_token_account;
        job.escrow_authority = ctx.accounts.escrow_authority.key();
        job.amount = args.amount;
        job.verifier_policy_id = args.verifier_policy_id;
        job.image_policy_id = args.image_policy_id;
        job.task_hash = args.task_hash;
        job.reward_script_hash = args.reward_script_hash;
        job.harbor_environment_hash = args.harbor_environment_hash;
        job.artifact_policy_hash = args.artifact_policy_hash;
        job.network_policy_hash = args.network_policy_hash;
        job.resource_class_hash = args.resource_class_hash;
        job.timeout_seconds = args.timeout_seconds;
        job.expires_at = args.expires_at;
        job.lease = Pubkey::default();
        job.status = JOB_STATUS_OPEN;
        job.bump = ctx.bumps.job;
        job.escrow_authority_bump = ctx.bumps.escrow_authority;

        config.job_count = config
            .job_count
            .checked_add(1)
            .ok_or(SolrlError::MathOverflow)?;

        emit!(JobCreated {
            job: job.key(),
            job_id,
            owner: job.owner,
            amount: job.amount,
        });

        Ok(())
    }

    pub fn create_lease(
        ctx: Context<CreateLease>,
        lease_id: [u8; 32],
        args: CreateLeaseArgs,
    ) -> Result<()> {
        let now = Clock::get()?.unix_timestamp;
        ctx.accounts.config.require_not_paused()?;

        require!(
            ctx.accounts.operator.status == OPERATOR_STATUS_ACTIVE,
            SolrlError::InactiveOperator
        );
        require!(
            ctx.accounts.operator.stake_amount >= ctx.accounts.config.min_operator_stake,
            SolrlError::InsufficientStake
        );
        require!(
            ctx.accounts.job.status == JOB_STATUS_OPEN,
            SolrlError::JobNotOpen
        );
        require!(args.expires_at > now, SolrlError::Expired);

        let lease = &mut ctx.accounts.lease;
        lease.config = ctx.accounts.config.key();
        lease.lease_id = lease_id;
        lease.job = ctx.accounts.job.key();
        lease.operator = ctx.accounts.operator.key();
        lease.payout_token_account = ctx.accounts.operator.payout_token_account;
        lease.nonce = args.nonce;
        lease.expected_pcr16 = args.expected_pcr16;
        lease.expected_worker_public_key_hash = args.expected_worker_public_key_hash;
        lease.expires_at = args.expires_at;
        lease.status = LEASE_STATUS_ACTIVE;
        lease.bump = ctx.bumps.lease;

        ctx.accounts.job.lease = lease.key();
        ctx.accounts.job.status = JOB_STATUS_LEASED;
        ctx.accounts.operator.active_lease_count = ctx
            .accounts
            .operator
            .active_lease_count
            .checked_add(1)
            .ok_or(SolrlError::MathOverflow)?;

        emit!(LeaseCreated {
            lease: lease.key(),
            job: lease.job,
            operator: lease.operator,
            nonce: lease.nonce,
        });

        Ok(())
    }

    pub fn settle_claim(
        ctx: Context<SettleClaim>,
        claim: ClaimV1,
        signature_instruction_index: u8,
    ) -> Result<()> {
        validate_claim(&ctx, &claim, signature_instruction_index)?;

        let claim_digest = claim_hash(&claim).map_err(|_| SolrlError::InvalidClaimEncoding)?;
        {
            let receipt = &mut ctx.accounts.claim_receipt;
            receipt.config = ctx.accounts.config.key();
            receipt.job = ctx.accounts.job.key();
            receipt.lease = ctx.accounts.lease.key();
            receipt.operator = ctx.accounts.operator.key();
            receipt.nonce = claim.nonce;
            receipt.claim_hash = claim_digest;
            receipt.payout_token_account = claim.payout_token_account;
            receipt.amount = claim.amount;
            receipt.reward_value = claim.reward_value;
            receipt.verifier = ctx.accounts.verifier.key();
            receipt.verifier_policy = ctx.accounts.verifier_policy.key();
            receipt.image_policy = ctx.accounts.image_policy.key();
            receipt.attestation_document_hash = claim.attestation_document_hash;
            receipt.trajectory_hash = claim.trajectory_hash;
            receipt.expires_at = claim.claim_expiry_unix;
            receipt.created_slot = Clock::get()?.slot;
            receipt.status = RECEIPT_STATUS_PENDING;
            receipt.bump = ctx.bumps.claim_receipt;
        }

        {
            let nonce_receipt = &mut ctx.accounts.nonce_receipt;
            nonce_receipt.config = ctx.accounts.config.key();
            nonce_receipt.nonce = claim.nonce;
            nonce_receipt.claim_receipt = ctx.accounts.claim_receipt.key();
            nonce_receipt.consumed = false;
            nonce_receipt.bump = ctx.bumps.nonce_receipt;
        }

        transfer_escrow_to_operator(&ctx, claim.amount)?;
        mark_claim_paid(ctx.accounts);

        emit!(ClaimSettled {
            claim_receipt: ctx.accounts.claim_receipt.key(),
            job: ctx.accounts.claim_receipt.job,
            amount: claim.amount,
            claim_hash: ctx.accounts.claim_receipt.claim_hash,
        });

        Ok(())
    }

    pub fn transfer_hook(ctx: Context<TransferHook>, _amount: u64) -> Result<()> {
        assert_transferring(&ctx.accounts.source_token)?;
        ctx.accounts.config.require_not_paused()?;
        require_keys_eq!(
            ctx.accounts.mint.key(),
            ctx.accounts.config.token_mint,
            SolrlError::InvalidMint
        );
        Ok(())
    }

    pub fn slash_operator(
        ctx: Context<SlashOperator>,
        slash_claim: SlashClaimV1,
        signature_instruction_index: u8,
    ) -> Result<()> {
        ctx.accounts.config.require_not_paused()?;
        validate_slash_claim(&ctx, &slash_claim, signature_instruction_index)?;

        ctx.accounts.operator.stake_amount = ctx
            .accounts
            .operator
            .stake_amount
            .checked_sub(slash_claim.slash_amount)
            .ok_or(SolrlError::InsufficientStake)?;
        ctx.accounts.operator.total_slashed = ctx
            .accounts
            .operator
            .total_slashed
            .checked_add(slash_claim.slash_amount)
            .ok_or(SolrlError::MathOverflow)?;
        ctx.accounts.operator.slash_count = ctx
            .accounts
            .operator
            .slash_count
            .checked_add(1)
            .ok_or(SolrlError::MathOverflow)?;
        ctx.accounts.lease.status = LEASE_STATUS_SLASHED;

        transfer_stake_to_treasury(&ctx, slash_claim.slash_amount)?;

        emit!(OperatorSlashed {
            operator: ctx.accounts.operator.key(),
            amount: slash_claim.slash_amount,
            reason_code: slash_claim.reason_code,
            claim_hash: slash_claim.claim_hash,
        });

        Ok(())
    }
}

fn validate_claim(ctx: &Context<SettleClaim>, claim: &ClaimV1, signature_index: u8) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let claim_digest = claim_hash(claim).map_err(|_| SolrlError::InvalidClaimEncoding)?;
    let claim_bytes = claim_message(claim).map_err(|_| SolrlError::InvalidClaimEncoding)?;

    require!(
        claim.protocol_version == CLAIM_PROTOCOL_VERSION,
        SolrlError::UnsupportedProtocolVersion
    );
    require!(
        claim.cluster_hash == ctx.accounts.config.cluster_hash,
        SolrlError::InvalidCluster
    );
    require_keys_eq!(claim.program_id, crate::ID, SolrlError::InvalidClaim);
    require_keys_eq!(claim.hook_program_id, crate::ID, SolrlError::InvalidClaim);
    require_keys_eq!(
        claim.token_mint,
        ctx.accounts.config.token_mint,
        SolrlError::InvalidMint
    );
    require_keys_eq!(
        claim.job_account,
        ctx.accounts.job.key(),
        SolrlError::InvalidJob
    );
    require_keys_eq!(
        claim.lease_account,
        ctx.accounts.lease.key(),
        SolrlError::InvalidLease
    );
    require_keys_eq!(
        claim.claim_receipt_account,
        ctx.accounts.claim_receipt.key(),
        SolrlError::InvalidClaimReceipt
    );
    require_keys_eq!(
        claim.operator_account,
        ctx.accounts.operator.key(),
        SolrlError::InvalidOperator
    );
    require_keys_eq!(
        claim.payout_token_account,
        ctx.accounts.operator.payout_token_account,
        SolrlError::InvalidPayoutAccount
    );

    require!(
        ctx.accounts.job.status == JOB_STATUS_LEASED,
        SolrlError::JobNotOpen
    );
    require_keys_eq!(
        ctx.accounts.job.lease,
        ctx.accounts.lease.key(),
        SolrlError::InvalidLease
    );
    require!(
        ctx.accounts.job.amount == claim.amount,
        SolrlError::InvalidAmount
    );
    require!(
        ctx.accounts.job.verifier_policy_id == claim.verifier_policy_id,
        SolrlError::InvalidVerifierPolicy
    );
    require!(
        ctx.accounts.job.image_policy_id == claim.image_policy_id,
        SolrlError::InvalidImagePolicy
    );
    require!(
        ctx.accounts.job.task_hash == claim.task_hash,
        SolrlError::InvalidTaskHash
    );
    require!(
        ctx.accounts.job.reward_script_hash == claim.reward_script_hash,
        SolrlError::InvalidRewardScriptHash
    );
    require!(
        ctx.accounts.job.harbor_environment_hash == claim.harbor_environment_hash,
        SolrlError::InvalidHarborEnvironmentHash
    );
    require!(
        ctx.accounts.job.artifact_policy_hash == claim.artifact_policy_hash,
        SolrlError::InvalidArtifactPolicy
    );
    require!(
        ctx.accounts.job.network_policy_hash == claim.network_policy_hash,
        SolrlError::InvalidNetworkPolicyHash
    );
    require!(
        ctx.accounts.job.resource_class_hash == claim.resource_class_hash,
        SolrlError::InvalidResourceClass
    );
    require!(ctx.accounts.job.expires_at >= now, SolrlError::Expired);

    require!(
        ctx.accounts.lease.status == LEASE_STATUS_ACTIVE,
        SolrlError::LeaseNotActive
    );
    require_keys_eq!(
        ctx.accounts.lease.operator,
        ctx.accounts.operator.key(),
        SolrlError::InvalidOperator
    );
    require!(
        ctx.accounts.lease.nonce == claim.nonce,
        SolrlError::InvalidNonce
    );
    require!(
        ctx.accounts.lease.expected_pcr16 == claim.pcr16,
        SolrlError::InvalidPcr16
    );
    require!(
        ctx.accounts.lease.expected_worker_public_key_hash == claim.worker_public_key_hash,
        SolrlError::InvalidWorkerPublicKeyHash
    );
    require!(ctx.accounts.lease.expires_at >= now, SolrlError::Expired);
    require!(
        claim.lease_expiry_unix == ctx.accounts.lease.expires_at,
        SolrlError::Expired
    );
    require!(claim.claim_expiry_unix >= now, SolrlError::Expired);
    require!(claim.reward_value >= 0, SolrlError::InvalidRewardValue);

    require!(
        ctx.accounts.operator.status == OPERATOR_STATUS_ACTIVE,
        SolrlError::InactiveOperator
    );
    require!(
        ctx.accounts.operator.stake_amount >= ctx.accounts.config.min_operator_stake,
        SolrlError::InsufficientStake
    );

    require!(
        ctx.accounts.verifier_policy.active,
        SolrlError::InactiveVerifierPolicy
    );
    require!(
        ctx.accounts.verifier_policy.verifier_policy_id == claim.verifier_policy_id,
        SolrlError::InvalidVerifierPolicy
    );
    require!(ctx.accounts.verifier.active, SolrlError::InactiveVerifier);
    require!(
        ctx.accounts.verifier.policy_id == claim.verifier_policy_id,
        SolrlError::InvalidVerifierPolicy
    );
    require!(
        ctx.accounts.verifier.family == ctx.accounts.verifier_policy.family,
        SolrlError::InvalidVerifierPolicy
    );
    require!(
        ctx.accounts.verifier.version >= ctx.accounts.verifier_policy.min_version,
        SolrlError::InvalidVerifierPolicy
    );

    require!(
        ctx.accounts.image_policy.active,
        SolrlError::InactiveImagePolicy
    );
    require!(
        ctx.accounts.image_policy.image_policy_id == claim.image_policy_id,
        SolrlError::InvalidImagePolicy
    );
    require!(
        ctx.accounts.image_policy.pcr16 == claim.pcr16,
        SolrlError::InvalidPcr16
    );
    require_nonzero(
        &claim.attestation_document_hash,
        SolrlError::InvalidAttestationHash,
    )?;
    require_nonzero(&claim.trajectory_hash, SolrlError::InvalidTrajectoryHash)?;

    verify_ed25519_instruction(
        &ctx.accounts.instructions.to_account_info(),
        signature_index,
        &ctx.accounts.verifier.ed25519_pubkey,
        &claim_bytes,
    )?;
    require_nonzero(&claim_digest, SolrlError::InvalidClaimEncoding)?;

    Ok(())
}

fn validate_slash_claim(
    ctx: &Context<SlashOperator>,
    claim: &SlashClaimV1,
    signature_index: u8,
) -> Result<()> {
    let now = Clock::get()?.unix_timestamp;
    let claim_bytes = slash_claim_message(claim).map_err(|_| SolrlError::InvalidClaimEncoding)?;
    let claim_digest = slash_claim_hash(claim).map_err(|_| SolrlError::InvalidClaimEncoding)?;

    require!(
        claim.protocol_version == CLAIM_PROTOCOL_VERSION,
        SolrlError::UnsupportedProtocolVersion
    );
    require!(
        claim.cluster_hash == ctx.accounts.config.cluster_hash,
        SolrlError::InvalidCluster
    );
    require_keys_eq!(claim.program_id, crate::ID, SolrlError::InvalidClaim);
    require_keys_eq!(
        claim.token_mint,
        ctx.accounts.config.token_mint,
        SolrlError::InvalidMint
    );
    require_keys_eq!(
        claim.operator_account,
        ctx.accounts.operator.key(),
        SolrlError::InvalidOperator
    );
    require_keys_eq!(
        claim.stake_token_account,
        ctx.accounts.operator.stake_token_account,
        SolrlError::InvalidStakeAccount
    );
    require_keys_eq!(
        claim.treasury_token_account,
        ctx.accounts.config.treasury_token_account,
        SolrlError::InvalidTreasury
    );
    require!(
        claim.verifier_policy_id == ctx.accounts.verifier_policy.verifier_policy_id,
        SolrlError::InvalidVerifierPolicy
    );
    require!(
        claim.verifier_id == ctx.accounts.verifier.verifier_id,
        SolrlError::InvalidVerifier
    );
    require_keys_eq!(
        claim.lease_account,
        ctx.accounts.lease.key(),
        SolrlError::InvalidLease
    );
    require_keys_eq!(
        claim.job_account,
        ctx.accounts.job.key(),
        SolrlError::InvalidJob
    );
    require!(claim.expires_unix >= now, SolrlError::Expired);
    require!(
        is_slashable_reason(claim.reason_code),
        SolrlError::RejectOnly
    );
    require!(claim.slash_amount > 0, SolrlError::InvalidAmount);
    require!(
        claim.slash_amount <= ctx.accounts.operator.stake_amount,
        SolrlError::InsufficientStake
    );
    require_nonzero(&claim.evidence_hash, SolrlError::InvalidEvidenceHash)?;

    require!(
        ctx.accounts.verifier_policy.active,
        SolrlError::InactiveVerifierPolicy
    );
    require!(ctx.accounts.verifier.active, SolrlError::InactiveVerifier);
    require!(
        ctx.accounts.verifier.policy_id == claim.verifier_policy_id,
        SolrlError::InvalidVerifierPolicy
    );
    verify_ed25519_instruction(
        &ctx.accounts.instructions.to_account_info(),
        signature_index,
        &ctx.accounts.verifier.ed25519_pubkey,
        &claim_bytes,
    )?;
    require_nonzero(&claim_digest, SolrlError::InvalidClaimEncoding)?;

    Ok(())
}

fn mark_claim_paid(ctx: &mut SettleClaim) {
    ctx.claim_receipt.status = RECEIPT_STATUS_CONSUMED;
    ctx.nonce_receipt.consumed = true;
    ctx.lease.status = LEASE_STATUS_SETTLED;
    ctx.job.status = JOB_STATUS_SETTLED;
    ctx.operator.active_lease_count = ctx.operator.active_lease_count.saturating_sub(1);
}

fn transfer_escrow_to_operator(ctx: &Context<SettleClaim>, amount: u64) -> Result<()> {
    let mut ix = spl_token_2022::instruction::transfer_checked(
        &ctx.accounts.token_program.key(),
        &ctx.accounts.escrow_token_account.key(),
        &ctx.accounts.token_mint.key(),
        &ctx.accounts.payout_token_account.key(),
        &ctx.accounts.escrow_authority.key(),
        &[],
        amount,
        ctx.accounts.config.token_decimals,
    )?;

    ix.accounts.push(AccountMeta::new_readonly(
        ctx.accounts.extra_account_meta_list.key(),
        false,
    ));
    ix.accounts
        .push(AccountMeta::new_readonly(ctx.accounts.config.key(), false));

    let job_key = ctx.accounts.job.key();
    let signer_seeds: &[&[&[u8]]] = &[&[
        b"escrow_authority",
        job_key.as_ref(),
        &[ctx.accounts.job.escrow_authority_bump],
    ]];

    invoke_signed(
        &ix,
        &[
            ctx.accounts.token_program.to_account_info(),
            ctx.accounts.escrow_token_account.to_account_info(),
            ctx.accounts.token_mint.to_account_info(),
            ctx.accounts.payout_token_account.to_account_info(),
            ctx.accounts.escrow_authority.to_account_info(),
            ctx.accounts.extra_account_meta_list.to_account_info(),
            ctx.accounts.config.to_account_info(),
        ],
        signer_seeds,
    )?;

    Ok(())
}

fn transfer_stake_to_treasury(ctx: &Context<SlashOperator>, amount: u64) -> Result<()> {
    let ix = spl_token_2022::instruction::transfer_checked(
        &ctx.accounts.token_program.key(),
        &ctx.accounts.stake_token_account.key(),
        &ctx.accounts.token_mint.key(),
        &ctx.accounts.treasury_token_account.key(),
        &ctx.accounts.stake_authority.key(),
        &[],
        amount,
        ctx.accounts.config.token_decimals,
    )?;
    let operator_key = ctx.accounts.operator.key();
    let signer_seeds: &[&[&[u8]]] = &[&[
        b"stake_authority",
        operator_key.as_ref(),
        &[ctx.accounts.operator.stake_authority_bump],
    ]];

    invoke_signed(
        &ix,
        &[
            ctx.accounts.token_program.to_account_info(),
            ctx.accounts.stake_token_account.to_account_info(),
            ctx.accounts.token_mint.to_account_info(),
            ctx.accounts.treasury_token_account.to_account_info(),
            ctx.accounts.stake_authority.to_account_info(),
        ],
        signer_seeds,
    )?;

    Ok(())
}

fn assert_transferring(source_token: &UncheckedAccount) -> Result<()> {
    let data = source_token.try_borrow_data()?;
    let account = StateWithExtensions::<Token2022Account>::unpack(&data)
        .map_err(|_| error!(SolrlError::InvalidTokenAccount))?;
    let extension = account
        .get_extension::<TransferHookAccount>()
        .map_err(|_| error!(SolrlError::InvalidHookCaller))?;
    require!(
        bool::from(extension.transferring),
        SolrlError::InvalidHookCaller
    );
    Ok(())
}

fn verify_ed25519_instruction(
    instructions: &AccountInfo,
    instruction_index: u8,
    public_key: &[u8; 32],
    message: &[u8],
) -> Result<()> {
    let ix = load_instruction_at_checked(instruction_index as usize, instructions)?;
    assert_ed25519_instruction_data(&ix, public_key, message)
}

fn assert_ed25519_instruction_data(
    ix: &Instruction,
    public_key: &[u8; 32],
    message: &[u8],
) -> Result<()> {
    require_keys_eq!(
        ix.program_id,
        ed25519_program::ID,
        SolrlError::InvalidSignatureInstruction
    );
    require!(ix.data.len() >= 16, SolrlError::InvalidSignatureInstruction);
    require!(ix.data[0] == 1, SolrlError::InvalidSignatureInstruction);

    let signature_offset = read_u16(&ix.data, 2)? as usize;
    let signature_instruction_index = read_u16(&ix.data, 4)?;
    let public_key_offset = read_u16(&ix.data, 6)? as usize;
    let public_key_instruction_index = read_u16(&ix.data, 8)?;
    let message_offset = read_u16(&ix.data, 10)? as usize;
    let message_size = read_u16(&ix.data, 12)? as usize;
    let message_instruction_index = read_u16(&ix.data, 14)?;

    require!(
        signature_instruction_index == u16::MAX,
        SolrlError::InvalidSignatureInstruction
    );
    require!(
        public_key_instruction_index == u16::MAX,
        SolrlError::InvalidSignatureInstruction
    );
    require!(
        message_instruction_index == u16::MAX,
        SolrlError::InvalidSignatureInstruction
    );
    require!(
        message_size == message.len(),
        SolrlError::InvalidSignatureMessage
    );

    let public_key_end = public_key_offset
        .checked_add(32)
        .ok_or(SolrlError::InvalidSignatureInstruction)?;
    let message_end = message_offset
        .checked_add(message_size)
        .ok_or(SolrlError::InvalidSignatureInstruction)?;
    let signature_end = signature_offset
        .checked_add(64)
        .ok_or(SolrlError::InvalidSignatureInstruction)?;

    require!(
        public_key_end <= ix.data.len(),
        SolrlError::InvalidSignatureInstruction
    );
    require!(
        message_end <= ix.data.len(),
        SolrlError::InvalidSignatureInstruction
    );
    require!(
        signature_end <= ix.data.len(),
        SolrlError::InvalidSignatureInstruction
    );
    require!(
        &ix.data[public_key_offset..public_key_end] == public_key,
        SolrlError::InvalidSignaturePublicKey
    );
    require!(
        &ix.data[message_offset..message_end] == message,
        SolrlError::InvalidSignatureMessage
    );

    Ok(())
}

fn read_u16(data: &[u8], offset: usize) -> Result<u16> {
    let end = offset
        .checked_add(2)
        .ok_or(SolrlError::InvalidSignatureInstruction)?;
    require!(end <= data.len(), SolrlError::InvalidSignatureInstruction);
    Ok(u16::from_le_bytes([data[offset], data[offset + 1]]))
}

fn is_slashable_reason(reason: u8) -> bool {
    matches!(
        reason,
        SLASH_REASON_REPLAY
            | SLASH_REASON_MALICIOUS_DUPLICATE
            | SLASH_REASON_WRONG_JOB
            | SLASH_REASON_WRONG_POLICY
            | SLASH_REASON_FORGED_CONTEXT
            | SLASH_REASON_LEASE_ABUSE
    )
}

fn require_nonzero(bytes: &[u8], error: SolrlError) -> Result<()> {
    if !bytes.iter().any(|byte| *byte != 0) {
        return Err(error.into());
    }
    Ok(())
}

#[derive(Accounts)]
pub struct InitializeConfig<'info> {
    #[account(init, payer = admin, space = 8 + Config::LEN, seeds = [b"config"], bump)]
    pub config: Box<Account<'info, Config>>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct AdminConfig<'info> {
    #[account(mut, seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    pub admin: Signer<'info>,
}

#[derive(Accounts)]
pub struct InitializeExtraAccountMetaList<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        mut,
        seeds = [b"extra-account-metas", mint.key().as_ref()],
        bump
    )]
    /// CHECK: SPL transfer hook validation account.
    pub extra_account_meta_list: UncheckedAccount<'info>,
    /// CHECK: Token-2022 mint address used as PDA seed.
    pub mint: UncheckedAccount<'info>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(verifier_policy_id: [u8; 32])]
pub struct RegisterVerifierPolicy<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = admin,
        space = 8 + VerifierPolicy::LEN,
        seeds = [b"verifier_policy", config.key().as_ref(), verifier_policy_id.as_ref()],
        bump
    )]
    pub verifier_policy: Account<'info, VerifierPolicy>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(verifier_id: [u8; 32])]
pub struct RegisterVerifier<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = admin,
        space = 8 + Verifier::LEN,
        seeds = [b"verifier", config.key().as_ref(), verifier_id.as_ref()],
        bump
    )]
    pub verifier: Account<'info, Verifier>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(image_policy_id: [u8; 32])]
pub struct RegisterImagePolicy<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = admin,
        space = 8 + ImagePolicy::LEN,
        seeds = [b"image_policy", config.key().as_ref(), image_policy_id.as_ref()],
        bump
    )]
    pub image_policy: Account<'info, ImagePolicy>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct RegisterOperator<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = owner,
        space = 8 + Operator::LEN,
        seeds = [b"operator", config.key().as_ref(), owner.key().as_ref()],
        bump
    )]
    pub operator: Account<'info, Operator>,
    #[account(seeds = [b"stake_authority", operator.key().as_ref()], bump)]
    /// CHECK: PDA authority expected to own the operator stake token account.
    pub stake_authority: UncheckedAccount<'info>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(job_id: [u8; 32])]
pub struct CreateJob<'info> {
    #[account(mut, seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(
        init,
        payer = owner,
        space = 8 + Job::LEN,
        seeds = [b"job", config.key().as_ref(), job_id.as_ref()],
        bump
    )]
    pub job: Box<Account<'info, Job>>,
    #[account(seeds = [b"escrow_authority", job.key().as_ref()], bump)]
    /// CHECK: PDA authority expected to own the job escrow token account.
    pub escrow_authority: UncheckedAccount<'info>,
    #[account(mut)]
    pub owner: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(lease_id: [u8; 32])]
pub struct CreateLease<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    #[account(mut, has_one = config)]
    pub job: Account<'info, Job>,
    #[account(mut, has_one = config)]
    pub operator: Account<'info, Operator>,
    #[account(
        init,
        payer = payer,
        space = 8 + Lease::LEN,
        seeds = [b"lease", job.key().as_ref(), lease_id.as_ref()],
        bump
    )]
    pub lease: Box<Account<'info, Lease>>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
#[instruction(claim: ClaimV1)]
pub struct SettleClaim<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Box<Account<'info, Config>>,
    #[account(mut, has_one = config)]
    pub job: Box<Account<'info, Job>>,
    #[account(mut, has_one = config, has_one = job)]
    pub lease: Box<Account<'info, Lease>>,
    #[account(mut, has_one = config)]
    pub operator: Box<Account<'info, Operator>>,
    #[account(has_one = config)]
    pub verifier: Box<Account<'info, Verifier>>,
    #[account(has_one = config)]
    pub verifier_policy: Box<Account<'info, VerifierPolicy>>,
    #[account(has_one = config)]
    pub image_policy: Box<Account<'info, ImagePolicy>>,
    #[account(
        init,
        payer = payer,
        space = 8 + ClaimReceipt::LEN,
        seeds = [b"claim_receipt", lease.key().as_ref(), claim.nonce.as_ref()],
        bump
    )]
    pub claim_receipt: Box<Account<'info, ClaimReceipt>>,
    #[account(
        init,
        payer = payer,
        space = 8 + NonceReceipt::LEN,
        seeds = [b"nonce", config.key().as_ref(), claim.nonce.as_ref()],
        bump
    )]
    pub nonce_receipt: Box<Account<'info, NonceReceipt>>,
    #[account(mut, address = job.escrow_token_account)]
    /// CHECK: Token-2022 escrow token account.
    pub escrow_token_account: UncheckedAccount<'info>,
    #[account(mut, address = operator.payout_token_account)]
    /// CHECK: Token-2022 operator payout token account.
    pub payout_token_account: UncheckedAccount<'info>,
    #[account(address = config.token_mint)]
    /// CHECK: Token-2022 mint.
    pub token_mint: UncheckedAccount<'info>,
    #[account(address = job.escrow_authority)]
    /// CHECK: PDA signer for escrow transfers.
    pub escrow_authority: UncheckedAccount<'info>,
    #[account(seeds = [b"extra-account-metas", token_mint.key().as_ref()], bump)]
    /// CHECK: SPL transfer hook validation account.
    pub extra_account_meta_list: UncheckedAccount<'info>,
    #[account(address = spl_token_2022::ID)]
    /// CHECK: Token-2022 program.
    pub token_program: UncheckedAccount<'info>,
    #[account(address = INSTRUCTIONS_ID)]
    /// CHECK: Solana instructions sysvar. The program only reads it.
    pub instructions: UncheckedAccount<'info>,
    #[account(mut)]
    pub payer: Signer<'info>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct TransferHook<'info> {
    /// CHECK: Token-2022 source token account.
    pub source_token: UncheckedAccount<'info>,
    /// CHECK: Token-2022 mint.
    pub mint: UncheckedAccount<'info>,
    /// CHECK: Token-2022 destination token account.
    pub destination_token: UncheckedAccount<'info>,
    /// CHECK: Source token account authority.
    pub owner: UncheckedAccount<'info>,
    #[account(seeds = [b"extra-account-metas", mint.key().as_ref()], bump)]
    /// CHECK: SPL transfer hook validation account.
    pub extra_account_meta_list: UncheckedAccount<'info>,
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Box<Account<'info, Config>>,
}

#[derive(Accounts)]
#[instruction(slash_claim: SlashClaimV1)]
pub struct SlashOperator<'info> {
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Box<Account<'info, Config>>,
    #[account(mut, has_one = config)]
    pub operator: Box<Account<'info, Operator>>,
    #[account(mut, has_one = config)]
    pub lease: Box<Account<'info, Lease>>,
    #[account(has_one = config)]
    pub job: Box<Account<'info, Job>>,
    #[account(has_one = config)]
    pub verifier: Box<Account<'info, Verifier>>,
    #[account(has_one = config)]
    pub verifier_policy: Box<Account<'info, VerifierPolicy>>,
    #[account(mut, address = operator.stake_token_account)]
    /// CHECK: Token-2022 operator stake vault token account.
    pub stake_token_account: UncheckedAccount<'info>,
    #[account(mut, address = config.treasury_token_account)]
    /// CHECK: Token-2022 treasury token account.
    pub treasury_token_account: UncheckedAccount<'info>,
    #[account(address = config.token_mint)]
    /// CHECK: Token-2022 mint.
    pub token_mint: UncheckedAccount<'info>,
    #[account(address = operator.stake_authority)]
    /// CHECK: PDA signer for stake vault transfers.
    pub stake_authority: UncheckedAccount<'info>,
    #[account(address = spl_token_2022::ID)]
    /// CHECK: Token-2022 program.
    pub token_program: UncheckedAccount<'info>,
    #[account(address = INSTRUCTIONS_ID)]
    /// CHECK: Solana instructions sysvar. The program only reads it.
    pub instructions: UncheckedAccount<'info>,
}

#[account]
pub struct Config {
    pub admin: Pubkey,
    pub cluster_hash: [u8; 32],
    pub token_mint: Pubkey,
    pub treasury_token_account: Pubkey,
    pub token_decimals: u8,
    pub min_operator_stake: u64,
    pub paused: bool,
    pub job_count: u64,
    pub bump: u8,
}

impl Config {
    pub const LEN: usize = 32 + 32 + 32 + 32 + 1 + 8 + 1 + 8 + 1;

    pub fn require_admin(&self, admin: &Signer) -> Result<()> {
        require_keys_eq!(self.admin, admin.key(), SolrlError::Unauthorized);
        Ok(())
    }

    pub fn require_not_paused(&self) -> Result<()> {
        require!(!self.paused, SolrlError::Paused);
        Ok(())
    }
}

#[account]
pub struct VerifierPolicy {
    pub config: Pubkey,
    pub verifier_policy_id: [u8; 32],
    pub family: [u8; 16],
    pub min_version: u16,
    pub active: bool,
    pub bump: u8,
}

impl VerifierPolicy {
    pub const LEN: usize = 32 + 32 + 16 + 2 + 1 + 1;
}

#[account]
pub struct Verifier {
    pub config: Pubkey,
    pub verifier_id: [u8; 32],
    pub ed25519_pubkey: [u8; 32],
    pub family: [u8; 16],
    pub version: u16,
    pub policy_id: [u8; 32],
    pub active: bool,
    pub bump: u8,
}

impl Verifier {
    pub const LEN: usize = 32 + 32 + 32 + 16 + 2 + 32 + 1 + 1;
}

#[account]
pub struct ImagePolicy {
    pub config: Pubkey,
    pub image_policy_id: [u8; 32],
    pub family: [u8; 16],
    pub version: u16,
    pub pcr0: [u8; 48],
    pub pcr1: [u8; 48],
    pub pcr2: [u8; 48],
    pub pcr16: [u8; 48],
    pub active: bool,
    pub bump: u8,
}

impl ImagePolicy {
    pub const LEN: usize = 32 + 32 + 16 + 2 + 48 + 48 + 48 + 48 + 1 + 1;
}

#[account]
pub struct Operator {
    pub config: Pubkey,
    pub owner: Pubkey,
    pub payout_token_account: Pubkey,
    pub stake_token_account: Pubkey,
    pub stake_authority: Pubkey,
    pub stake_amount: u64,
    pub active_lease_count: u64,
    pub total_slashed: u64,
    pub slash_count: u64,
    pub status: u8,
    pub bump: u8,
    pub stake_authority_bump: u8,
}

impl Operator {
    pub const LEN: usize = 32 + 32 + 32 + 32 + 32 + 8 + 8 + 8 + 8 + 1 + 1 + 1;
}

#[account]
pub struct Job {
    pub config: Pubkey,
    pub job_id: [u8; 32],
    pub owner: Pubkey,
    pub escrow_token_account: Pubkey,
    pub escrow_authority: Pubkey,
    pub amount: u64,
    pub verifier_policy_id: [u8; 32],
    pub image_policy_id: [u8; 32],
    pub task_hash: [u8; 32],
    pub reward_script_hash: [u8; 32],
    pub harbor_environment_hash: [u8; 32],
    pub artifact_policy_hash: [u8; 32],
    pub network_policy_hash: [u8; 32],
    pub resource_class_hash: [u8; 32],
    pub timeout_seconds: u32,
    pub expires_at: i64,
    pub lease: Pubkey,
    pub status: u8,
    pub bump: u8,
    pub escrow_authority_bump: u8,
}

impl Job {
    pub const LEN: usize =
        32 + 32 + 32 + 32 + 32 + 8 + 32 + 32 + 32 + 32 + 32 + 32 + 32 + 32 + 4 + 8 + 32 + 1 + 1 + 1;
}

#[account]
pub struct Lease {
    pub config: Pubkey,
    pub lease_id: [u8; 32],
    pub job: Pubkey,
    pub operator: Pubkey,
    pub payout_token_account: Pubkey,
    pub nonce: [u8; 32],
    pub expected_pcr16: [u8; 48],
    pub expected_worker_public_key_hash: [u8; 32],
    pub expires_at: i64,
    pub status: u8,
    pub bump: u8,
}

impl Lease {
    pub const LEN: usize = 32 + 32 + 32 + 32 + 32 + 32 + 48 + 32 + 8 + 1 + 1;
}

#[account]
pub struct ClaimReceipt {
    pub config: Pubkey,
    pub job: Pubkey,
    pub lease: Pubkey,
    pub operator: Pubkey,
    pub nonce: [u8; 32],
    pub claim_hash: [u8; 32],
    pub payout_token_account: Pubkey,
    pub amount: u64,
    pub reward_value: i64,
    pub verifier: Pubkey,
    pub verifier_policy: Pubkey,
    pub image_policy: Pubkey,
    pub attestation_document_hash: [u8; 32],
    pub trajectory_hash: [u8; 32],
    pub expires_at: i64,
    pub created_slot: u64,
    pub status: u8,
    pub bump: u8,
}

impl ClaimReceipt {
    pub const LEN: usize =
        32 + 32 + 32 + 32 + 32 + 32 + 32 + 8 + 8 + 32 + 32 + 32 + 32 + 32 + 8 + 8 + 1 + 1;
}

#[account]
pub struct NonceReceipt {
    pub config: Pubkey,
    pub nonce: [u8; 32],
    pub claim_receipt: Pubkey,
    pub consumed: bool,
    pub bump: u8,
}

impl NonceReceipt {
    pub const LEN: usize = 32 + 32 + 32 + 1 + 1;
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct InitializeConfigArgs {
    pub cluster_hash: [u8; 32],
    pub token_mint: Pubkey,
    pub treasury_token_account: Pubkey,
    pub token_decimals: u8,
    pub min_operator_stake: u64,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct RegisterVerifierPolicyArgs {
    pub family: [u8; 16],
    pub min_version: u16,
    pub active: bool,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct RegisterVerifierArgs {
    pub ed25519_pubkey: [u8; 32],
    pub family: [u8; 16],
    pub version: u16,
    pub policy_id: [u8; 32],
    pub active: bool,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct RegisterImagePolicyArgs {
    pub family: [u8; 16],
    pub version: u16,
    pub pcr0: [u8; 48],
    pub pcr1: [u8; 48],
    pub pcr2: [u8; 48],
    pub pcr16: [u8; 48],
    pub active: bool,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct RegisterOperatorArgs {
    pub payout_token_account: Pubkey,
    pub stake_token_account: Pubkey,
    pub stake_amount: u64,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct CreateJobArgs {
    pub escrow_token_account: Pubkey,
    pub amount: u64,
    pub verifier_policy_id: [u8; 32],
    pub image_policy_id: [u8; 32],
    pub task_hash: [u8; 32],
    pub reward_script_hash: [u8; 32],
    pub harbor_environment_hash: [u8; 32],
    pub artifact_policy_hash: [u8; 32],
    pub network_policy_hash: [u8; 32],
    pub resource_class_hash: [u8; 32],
    pub timeout_seconds: u32,
    pub expires_at: i64,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone)]
pub struct CreateLeaseArgs {
    pub nonce: [u8; 32],
    pub expected_pcr16: [u8; 48],
    pub expected_worker_public_key_hash: [u8; 32],
    pub expires_at: i64,
}

#[event]
pub struct ConfigInitialized {
    pub config: Pubkey,
    pub admin: Pubkey,
    pub token_mint: Pubkey,
    pub cluster_hash: [u8; 32],
}

#[event]
pub struct ConfigPauseSet {
    pub config: Pubkey,
    pub paused: bool,
}

#[event]
pub struct VerifierPolicyRegistered {
    pub verifier_policy: Pubkey,
    pub verifier_policy_id: [u8; 32],
    pub active: bool,
}

#[event]
pub struct VerifierRegistered {
    pub verifier: Pubkey,
    pub verifier_id: [u8; 32],
    pub active: bool,
}

#[event]
pub struct ImagePolicyRegistered {
    pub image_policy: Pubkey,
    pub image_policy_id: [u8; 32],
    pub active: bool,
}

#[event]
pub struct OperatorRegistered {
    pub operator: Pubkey,
    pub owner: Pubkey,
    pub stake_amount: u64,
}

#[event]
pub struct JobCreated {
    pub job: Pubkey,
    pub job_id: [u8; 32],
    pub owner: Pubkey,
    pub amount: u64,
}

#[event]
pub struct LeaseCreated {
    pub lease: Pubkey,
    pub job: Pubkey,
    pub operator: Pubkey,
    pub nonce: [u8; 32],
}

#[event]
pub struct ClaimSettled {
    pub claim_receipt: Pubkey,
    pub job: Pubkey,
    pub amount: u64,
    pub claim_hash: [u8; 32],
}

#[event]
pub struct OperatorSlashed {
    pub operator: Pubkey,
    pub amount: u64,
    pub reason_code: u8,
    pub claim_hash: [u8; 32],
}

#[error_code]
pub enum SolrlError {
    #[msg("admin authority is required")]
    Unauthorized,
    #[msg("protocol is paused")]
    Paused,
    #[msg("amount must be non-zero and match the job")]
    InvalidAmount,
    #[msg("checked arithmetic overflowed")]
    MathOverflow,
    #[msg("claim, lease, slash claim, or job is expired")]
    Expired,
    #[msg("claim uses an unsupported protocol version")]
    UnsupportedProtocolVersion,
    #[msg("claim encoding failed")]
    InvalidClaimEncoding,
    #[msg("cluster hash mismatch")]
    InvalidCluster,
    #[msg("claim fields do not match registry state")]
    InvalidClaim,
    #[msg("claim mint does not match registry mint")]
    InvalidMint,
    #[msg("claim job does not match job account")]
    InvalidJob,
    #[msg("lease mismatch")]
    InvalidLease,
    #[msg("lease is not active")]
    LeaseNotActive,
    #[msg("claim receipt account mismatch")]
    InvalidClaimReceipt,
    #[msg("operator mismatch")]
    InvalidOperator,
    #[msg("operator is inactive")]
    InactiveOperator,
    #[msg("operator stake is below required threshold")]
    InsufficientStake,
    #[msg("stake token account mismatch")]
    InvalidStakeAccount,
    #[msg("treasury token account mismatch")]
    InvalidTreasury,
    #[msg("payout token account mismatch")]
    InvalidPayoutAccount,
    #[msg("job is not open or leased")]
    JobNotOpen,
    #[msg("verifier policy mismatch")]
    InvalidVerifierPolicy,
    #[msg("verifier is invalid")]
    InvalidVerifier,
    #[msg("verifier policy is inactive")]
    InactiveVerifierPolicy,
    #[msg("image policy mismatch")]
    InvalidImagePolicy,
    #[msg("task hash mismatch")]
    InvalidTaskHash,
    #[msg("reward script hash mismatch")]
    InvalidRewardScriptHash,
    #[msg("harbor environment hash mismatch")]
    InvalidHarborEnvironmentHash,
    #[msg("artifact policy hash mismatch")]
    InvalidArtifactPolicy,
    #[msg("resource class hash mismatch")]
    InvalidResourceClass,
    #[msg("network policy hash mismatch")]
    InvalidNetworkPolicyHash,
    #[msg("worker public key hash mismatch")]
    InvalidWorkerPublicKeyHash,
    #[msg("reward value is invalid")]
    InvalidRewardValue,
    #[msg("timeout must be non-zero")]
    InvalidTimeout,
    #[msg("PCR16 mismatch")]
    InvalidPcr16,
    #[msg("attestation document hash must be non-zero")]
    InvalidAttestationHash,
    #[msg("trajectory hash must be non-zero")]
    InvalidTrajectoryHash,
    #[msg("verifier is inactive")]
    InactiveVerifier,
    #[msg("image policy is inactive")]
    InactiveImagePolicy,
    #[msg("ed25519 instruction is invalid")]
    InvalidSignatureInstruction,
    #[msg("ed25519 instruction message mismatch")]
    InvalidSignatureMessage,
    #[msg("ed25519 instruction public key mismatch")]
    InvalidSignaturePublicKey,
    #[msg("claim nonce mismatch")]
    InvalidNonce,
    #[msg("claim was already consumed")]
    Replay,
    #[msg("escrow token account mismatch")]
    InvalidEscrow,
    #[msg("escrow authority mismatch")]
    InvalidEscrowAuthority,
    #[msg("token account is invalid")]
    InvalidTokenAccount,
    #[msg("transfer hook was not called by Token-2022 transfer flow")]
    InvalidHookCaller,
    #[msg("slash reason is reject-only or dispute-only")]
    RejectOnly,
    #[msg("slash evidence hash must be non-zero")]
    InvalidEvidenceHash,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn compact_ed25519_instruction(public_key: [u8; 32], message: &[u8]) -> Instruction {
        let public_key_offset: u16 = 16;
        let signature_offset: u16 = public_key_offset + 32;
        let message_offset: u16 = signature_offset + 64;

        let mut data = Vec::new();
        data.push(1);
        data.push(0);
        data.extend_from_slice(&signature_offset.to_le_bytes());
        data.extend_from_slice(&u16::MAX.to_le_bytes());
        data.extend_from_slice(&public_key_offset.to_le_bytes());
        data.extend_from_slice(&u16::MAX.to_le_bytes());
        data.extend_from_slice(&message_offset.to_le_bytes());
        data.extend_from_slice(&(message.len() as u16).to_le_bytes());
        data.extend_from_slice(&u16::MAX.to_le_bytes());
        data.extend_from_slice(&public_key);
        data.extend_from_slice(&[99; 64]);
        data.extend_from_slice(message);

        Instruction {
            program_id: ed25519_program::ID,
            accounts: vec![],
            data,
        }
    }

    #[test]
    fn ed25519_instruction_parser_accepts_expected_key_and_message() {
        let public_key = [7; 32];
        let message = vec![8; 128];
        let ix = compact_ed25519_instruction(public_key, &message);

        assert!(assert_ed25519_instruction_data(&ix, &public_key, &message).is_ok());
    }

    #[test]
    fn ed25519_instruction_parser_rejects_wrong_key() {
        let public_key = [7; 32];
        let message = vec![8; 128];
        let ix = compact_ed25519_instruction(public_key, &message);
        let result = assert_ed25519_instruction_data(&ix, &[9; 32], &message);

        assert!(matches!(
            result,
            Err(err) if err == error!(SolrlError::InvalidSignaturePublicKey)
        ));
    }

    #[test]
    fn ed25519_instruction_parser_rejects_wrong_message() {
        let public_key = [7; 32];
        let message = vec![8; 128];
        let ix = compact_ed25519_instruction(public_key, &message);
        let result = assert_ed25519_instruction_data(&ix, &public_key, &[9; 128]);

        assert!(matches!(
            result,
            Err(err) if err == error!(SolrlError::InvalidSignatureMessage)
        ));
    }

    #[test]
    fn slash_reason_taxonomy_marks_only_slashable_reasons() {
        assert!(is_slashable_reason(SLASH_REASON_REPLAY));
        assert!(is_slashable_reason(SLASH_REASON_WRONG_POLICY));
        assert!(!is_slashable_reason(42));
    }
}
