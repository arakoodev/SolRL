use anchor_lang::prelude::borsh;
use anchor_lang::prelude::Pubkey;
use anchor_lang::{AnchorDeserialize, AnchorSerialize};
use sha2::{Digest, Sha384};

pub const CLAIM_DOMAIN: &[u8] = b"SOLRL_HARBOR_CLAIM_V1_FIXED";
pub const SLASH_CLAIM_DOMAIN: &[u8] = b"SOLRL_HARBOR_SLASH_CLAIM_V1_FIXED";
pub const PCR16_DOMAIN: &[u8] = b"SOLRL_HARBOR_PCR16_V1_FIXED";
pub const CLAIM_PROTOCOL_VERSION: u16 = 1;

pub const SLASH_REASON_REPLAY: u8 = 1;
pub const SLASH_REASON_MALICIOUS_DUPLICATE: u8 = 2;
pub const SLASH_REASON_WRONG_JOB: u8 = 3;
pub const SLASH_REASON_WRONG_POLICY: u8 = 4;
pub const SLASH_REASON_FORGED_CONTEXT: u8 = 5;
pub const SLASH_REASON_LEASE_ABUSE: u8 = 6;

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Debug, PartialEq, Eq)]
pub struct ClaimV1 {
    pub cluster_hash: [u8; 32],
    pub program_id: Pubkey,
    pub token_mint: Pubkey,
    pub hook_program_id: Pubkey,
    pub job_account: Pubkey,
    pub lease_account: Pubkey,
    pub claim_receipt_account: Pubkey,
    pub operator_account: Pubkey,
    pub payout_token_account: Pubkey,
    pub amount: u64,
    pub resource_class_hash: [u8; 32],
    pub verifier_policy_id: [u8; 32],
    pub image_policy_id: [u8; 32],
    pub worker_public_key_hash: [u8; 32],
    pub task_hash: [u8; 32],
    pub reward_script_hash: [u8; 32],
    pub harbor_environment_hash: [u8; 32],
    pub artifact_policy_hash: [u8; 32],
    pub network_policy_hash: [u8; 32],
    pub attestation_document_hash: [u8; 32],
    pub trajectory_hash: [u8; 32],
    pub pcr16: [u8; 48],
    pub reward_value: i64,
    pub lease_expiry_unix: i64,
    pub claim_expiry_unix: i64,
    pub nonce: [u8; 32],
    pub protocol_version: u16,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Debug, PartialEq, Eq)]
pub struct SlashClaimV1 {
    pub cluster_hash: [u8; 32],
    pub program_id: Pubkey,
    pub token_mint: Pubkey,
    pub operator_account: Pubkey,
    pub stake_token_account: Pubkey,
    pub treasury_token_account: Pubkey,
    pub verifier_policy_id: [u8; 32],
    pub verifier_id: [u8; 32],
    pub claim_hash: [u8; 32],
    pub lease_account: Pubkey,
    pub job_account: Pubkey,
    pub reason_code: u8,
    pub slash_amount: u64,
    pub evidence_hash: [u8; 32],
    pub nonce: [u8; 32],
    pub expires_unix: i64,
    pub protocol_version: u16,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, Debug, PartialEq, Eq)]
pub struct Pcr16Components {
    pub job_account: Pubkey,
    pub lease_account: Pubkey,
    pub nonce: [u8; 32],
    pub task_hash: [u8; 32],
    pub task_toml_hash: [u8; 32],
    pub instruction_hash: [u8; 32],
    pub test_hash: [u8; 32],
    pub reward_script_hash: [u8; 32],
    pub harbor_environment_hash: [u8; 32],
    pub resource_class_hash: [u8; 32],
    pub timeout_seconds: u32,
    pub network_policy_hash: [u8; 32],
    pub operator_account: Pubkey,
    pub payout_token_account: Pubkey,
    pub token_mint: Pubkey,
    pub artifact_policy_hash: [u8; 32],
    pub protocol_version: u16,
}

pub fn claim_message(claim: &ClaimV1) -> std::io::Result<Vec<u8>> {
    domain_message(CLAIM_DOMAIN, claim)
}

pub fn claim_hash(claim: &ClaimV1) -> std::io::Result<[u8; 32]> {
    hash_message(&claim_message(claim)?)
}

pub fn slash_claim_message(claim: &SlashClaimV1) -> std::io::Result<Vec<u8>> {
    domain_message(SLASH_CLAIM_DOMAIN, claim)
}

pub fn slash_claim_hash(claim: &SlashClaimV1) -> std::io::Result<[u8; 32]> {
    hash_message(&slash_claim_message(claim)?)
}

pub fn pcr16_preimage(components: &Pcr16Components) -> std::io::Result<Vec<u8>> {
    domain_message(PCR16_DOMAIN, components)
}

pub fn pcr16_digest(components: &Pcr16Components) -> std::io::Result<[u8; 48]> {
    let preimage = pcr16_preimage(components)?;
    let digest = Sha384::digest(preimage);
    let mut out = [0u8; 48];
    out.copy_from_slice(&digest);
    Ok(out)
}

fn domain_message<T: AnchorSerialize>(domain: &[u8], value: &T) -> std::io::Result<Vec<u8>> {
    let mut body = Vec::with_capacity(640);
    body.extend_from_slice(domain);
    body.push(0);
    value.serialize(&mut body)?;
    Ok(body)
}

fn hash_message(message: &[u8]) -> std::io::Result<[u8; 32]> {
    Ok(anchor_lang::solana_program::hash::hash(message).to_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pubkey(byte: u8) -> Pubkey {
        Pubkey::new_from_array([byte; 32])
    }

    fn hex_encode(bytes: &[u8]) -> String {
        const LUT: &[u8; 16] = b"0123456789abcdef";
        let mut out = String::with_capacity(bytes.len() * 2);
        for byte in bytes {
            out.push(LUT[(byte >> 4) as usize] as char);
            out.push(LUT[(byte & 0x0f) as usize] as char);
        }
        out
    }

    fn fixture_string(key: &str) -> std::io::Result<String> {
        let source = include_str!("../../../tests/fixtures/claim_v1_golden.json");
        let needle = format!("\"{key}\": \"");
        let start = source.find(&needle).ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("missing fixture key {key}"),
            )
        })? + needle.len();
        let rest = &source[start..];
        let end = rest.find('"').ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("unterminated fixture key {key}"),
            )
        })?;
        Ok(rest[..end].to_string())
    }

    fn sample_claim(nonce: [u8; 32]) -> ClaimV1 {
        ClaimV1 {
            cluster_hash: [1; 32],
            program_id: pubkey(2),
            token_mint: pubkey(3),
            hook_program_id: pubkey(4),
            job_account: pubkey(5),
            lease_account: pubkey(6),
            claim_receipt_account: pubkey(7),
            operator_account: pubkey(8),
            payout_token_account: pubkey(9),
            amount: 100,
            resource_class_hash: [10; 32],
            verifier_policy_id: [11; 32],
            image_policy_id: [12; 32],
            worker_public_key_hash: [13; 32],
            task_hash: [14; 32],
            reward_script_hash: [15; 32],
            harbor_environment_hash: [16; 32],
            artifact_policy_hash: [17; 32],
            network_policy_hash: [18; 32],
            attestation_document_hash: [19; 32],
            trajectory_hash: [20; 32],
            pcr16: [21; 48],
            reward_value: 1,
            lease_expiry_unix: 4_102_444_800,
            claim_expiry_unix: 4_102_444_800,
            nonce,
            protocol_version: CLAIM_PROTOCOL_VERSION,
        }
    }

    fn sample_slash_claim(nonce: [u8; 32]) -> SlashClaimV1 {
        SlashClaimV1 {
            cluster_hash: [1; 32],
            program_id: pubkey(2),
            token_mint: pubkey(3),
            operator_account: pubkey(4),
            stake_token_account: pubkey(5),
            treasury_token_account: pubkey(6),
            verifier_policy_id: [7; 32],
            verifier_id: [8; 32],
            claim_hash: [9; 32],
            lease_account: pubkey(10),
            job_account: pubkey(11),
            reason_code: SLASH_REASON_WRONG_JOB,
            slash_amount: 25,
            evidence_hash: [12; 32],
            nonce,
            expires_unix: 4_102_444_800,
            protocol_version: CLAIM_PROTOCOL_VERSION,
        }
    }

    #[test]
    fn claim_hash_is_stable_for_same_claim() -> std::io::Result<()> {
        let claim = sample_claim([42; 32]);

        assert_eq!(claim_hash(&claim)?, claim_hash(&claim)?);
        Ok(())
    }

    #[test]
    fn claim_hash_changes_when_nonce_changes() -> std::io::Result<()> {
        let a = sample_claim([1; 32]);
        let b = sample_claim([2; 32]);

        assert_ne!(claim_hash(&a)?, claim_hash(&b)?);
        Ok(())
    }

    #[test]
    fn claim_message_is_domain_separated() -> std::io::Result<()> {
        let claim = sample_claim([1; 32]);
        let message = claim_message(&claim)?;

        assert!(message.starts_with(CLAIM_DOMAIN));
        assert_eq!(message[CLAIM_DOMAIN.len()], 0);
        Ok(())
    }

    #[test]
    fn slash_claim_hash_uses_different_domain() -> std::io::Result<()> {
        let slash = sample_slash_claim([1; 32]);
        let claim = sample_claim([1; 32]);

        assert_ne!(slash_claim_hash(&slash)?, claim_hash(&claim)?);
        assert!(slash_claim_message(&slash)?.starts_with(SLASH_CLAIM_DOMAIN));
        Ok(())
    }

    #[test]
    fn pcr16_is_sha384_and_stable() -> std::io::Result<()> {
        let components = Pcr16Components {
            job_account: pubkey(1),
            lease_account: pubkey(2),
            nonce: [3; 32],
            task_hash: [4; 32],
            task_toml_hash: [5; 32],
            instruction_hash: [6; 32],
            test_hash: [7; 32],
            reward_script_hash: [8; 32],
            harbor_environment_hash: [9; 32],
            resource_class_hash: [10; 32],
            timeout_seconds: 900,
            network_policy_hash: [11; 32],
            operator_account: pubkey(12),
            payout_token_account: pubkey(13),
            token_mint: pubkey(14),
            artifact_policy_hash: [15; 32],
            protocol_version: CLAIM_PROTOCOL_VERSION,
        };

        assert_eq!(pcr16_digest(&components)?.len(), 48);
        assert_eq!(pcr16_digest(&components)?, pcr16_digest(&components)?);
        Ok(())
    }

    #[test]
    fn claim_v1_golden_vector_matches_rust_encoder() -> Result<(), Box<dyn std::error::Error>> {
        let claim = sample_claim([42; 32]);

        assert_eq!(
            hex_encode(&claim_hash(&claim)?),
            fixture_string("claim_hash")?
        );
        assert_eq!(
            hex_encode(&claim_message(&claim)?),
            fixture_string("claim_message_hex")?
        );
        Ok(())
    }
}
