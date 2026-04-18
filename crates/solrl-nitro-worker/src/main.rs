use aws_nitro_enclaves_nsm_api::{
    api::{Request, Response},
    driver::{nsm_exit, nsm_init, nsm_process_request},
};
use serde_bytes::ByteBuf;
use std::{
    collections::BTreeMap,
    env,
    fs::File,
    io::{Read, Write},
    mem,
    os::fd::FromRawFd,
    ptr,
};

const DEFAULT_VSOCK_PORT: u32 = 5005;
const MAX_REQUEST_BYTES: usize = 4096;

#[derive(Debug)]
struct AttestationRequest {
    user_data: Vec<u8>,
    public_key: Vec<u8>,
    nonce: Vec<u8>,
}

fn validate_hex(name: &str, value: &str) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    if value.is_empty()
        || value.len() % 2 != 0
        || !value.chars().all(|char| char.is_ascii_hexdigit())
    {
        return Err(format!("{name} must be non-empty even-length hex").into());
    }
    Ok(hex::decode(value)?)
}

fn parse_request(raw: &str) -> Result<AttestationRequest, Box<dyn std::error::Error>> {
    let mut values = BTreeMap::new();
    for line in raw.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            return Err(format!("invalid request line: {line}").into());
        };
        values.insert(key, value);
    }
    let Some(user_data_hex) = values.get("USER_DATA_HEX") else {
        return Err("missing USER_DATA_HEX".into());
    };
    let Some(public_key_hex) = values.get("PUBLIC_KEY_HEX") else {
        return Err("missing PUBLIC_KEY_HEX".into());
    };
    let Some(nonce_hex) = values.get("NONCE_HEX") else {
        return Err("missing NONCE_HEX".into());
    };
    Ok(AttestationRequest {
        user_data: validate_hex("USER_DATA_HEX", user_data_hex)?,
        public_key: validate_hex("PUBLIC_KEY_HEX", public_key_hex)?,
        nonce: validate_hex("NONCE_HEX", nonce_hex)?,
    })
}

fn listen(port: u32) -> Result<File, Box<dyn std::error::Error>> {
    let fd = unsafe { libc::socket(libc::AF_VSOCK, libc::SOCK_STREAM, 0) };
    if fd < 0 {
        return Err("failed to create vsock socket".into());
    }
    let addr = libc::sockaddr_vm {
        svm_family: libc::AF_VSOCK as libc::sa_family_t,
        svm_reserved1: 0,
        svm_port: port,
        svm_cid: u32::MAX,
        svm_zero: [0; 4],
    };
    let rc = unsafe {
        libc::bind(
            fd,
            &addr as *const libc::sockaddr_vm as *const libc::sockaddr,
            mem::size_of::<libc::sockaddr_vm>() as libc::socklen_t,
        )
    };
    if rc != 0 {
        return Err("failed to bind vsock listener".into());
    }
    if unsafe { libc::listen(fd, 1) } != 0 {
        return Err("failed to listen on vsock".into());
    }
    let client = unsafe { libc::accept(fd, ptr::null_mut(), ptr::null_mut()) };
    unsafe { libc::close(fd) };
    if client < 0 {
        return Err("failed to accept vsock client".into());
    }
    Ok(unsafe { File::from_raw_fd(client) })
}

fn recv_request(stream: &mut File) -> Result<AttestationRequest, Box<dyn std::error::Error>> {
    let mut buf = Vec::new();
    stream
        .take(MAX_REQUEST_BYTES as u64)
        .read_to_end(&mut buf)?;
    let raw = String::from_utf8(buf)?;
    parse_request(&raw)
}

fn request_attestation(request: AttestationRequest) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let nsm_fd = nsm_init();
    if nsm_fd < 0 {
        return Err("failed to initialize NSM".into());
    }
    let pcr16 = match nsm_process_request(
        nsm_fd,
        Request::ExtendPCR {
            index: 16,
            data: request.user_data.clone(),
        },
    ) {
        Response::ExtendPCR { data } => data,
        Response::Error(err) => {
            nsm_exit(nsm_fd);
            return Err(format!("failed to extend PCR16: {err:?}").into());
        }
        other => {
            nsm_exit(nsm_fd);
            return Err(format!("unexpected ExtendPCR response: {other:?}").into());
        }
    };
    match nsm_process_request(nsm_fd, Request::LockPCR { index: 16 }) {
        Response::LockPCR => {}
        Response::Error(err) => {
            nsm_exit(nsm_fd);
            return Err(format!("failed to lock PCR16: {err:?}").into());
        }
        other => {
            nsm_exit(nsm_fd);
            return Err(format!("unexpected LockPCR response: {other:?}").into());
        }
    };
    match nsm_process_request(nsm_fd, Request::DescribePCR { index: 16 }) {
        Response::DescribePCR { lock, data } if lock && data == pcr16 => {}
        Response::DescribePCR { lock, data } => {
            nsm_exit(nsm_fd);
            return Err(
                format!("PCR16 lock check failed: lock={lock}, bytes={}", data.len()).into(),
            );
        }
        Response::Error(err) => {
            nsm_exit(nsm_fd);
            return Err(format!("failed to describe PCR16: {err:?}").into());
        }
        other => {
            nsm_exit(nsm_fd);
            return Err(format!("unexpected DescribePCR response: {other:?}").into());
        }
    };
    let response = nsm_process_request(
        nsm_fd,
        Request::Attestation {
            public_key: Some(ByteBuf::from(request.public_key)),
            user_data: Some(ByteBuf::from(request.user_data)),
            nonce: Some(ByteBuf::from(request.nonce)),
        },
    );
    nsm_exit(nsm_fd);
    match response {
        Response::Attestation { document } => Ok(document),
        Response::Error(err) => Err(format!("failed to request attestation: {err:?}").into()),
        other => Err(format!("unexpected NSM response: {other:?}").into()),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let port = match env::var("SOLRL_VSOCK_PORT") {
        Ok(value) => value.parse()?,
        Err(_) => DEFAULT_VSOCK_PORT,
    };
    let mut stream = listen(port)?;
    let request = recv_request(&mut stream)?;
    let document = request_attestation(request)?;
    stream.write_all(hex::encode(document).as_bytes())?;
    stream.write_all(b"\n")?;
    Ok(())
}
