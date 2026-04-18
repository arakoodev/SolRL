use aws_nitro_enclaves_nsm_api::{
    api::{Request, Response},
    driver::{nsm_exit, nsm_init, nsm_process_request},
};
use serde_bytes::ByteBuf;
use std::{env, fs::File, io::Write, mem, os::fd::FromRawFd, ptr};

fn env_hex(name: &str) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    Ok(hex::decode(env::var(name)?)?)
}

fn serve_once(payload: &[u8], port: u32) -> Result<(), Box<dyn std::error::Error>> {
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
    if client < 0 {
        return Err("failed to accept vsock client".into());
    }
    let mut stream = unsafe { File::from_raw_fd(client) };
    stream.write_all(payload)?;
    stream.write_all(b"\n")?;
    unsafe { libc::close(fd) };
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let user_data = env_hex("SOLRL_USER_DATA_HEX")?;
    let public_key = env_hex("SOLRL_PUBLIC_KEY_HEX")?;
    let nonce = env_hex("SOLRL_NONCE_HEX")?;
    let port: u32 = env::var("SOLRL_VSOCK_PORT")?.parse()?;
    let nsm_fd = nsm_init();
    if nsm_fd < 0 {
        return Err("failed to initialize NSM".into());
    }
    let pcr16 = match nsm_process_request(nsm_fd, Request::ExtendPCR { index: 16, data: user_data.clone() }) {
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
            return Err(format!("PCR16 lock check failed: lock={lock}, bytes={}", data.len()).into());
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
            public_key: Some(ByteBuf::from(public_key)),
            user_data: Some(ByteBuf::from(user_data)),
            nonce: Some(ByteBuf::from(nonce)),
        },
    );
    nsm_exit(nsm_fd);
    let document = match response {
        Response::Attestation { document } => document,
        other => return Err(format!("unexpected NSM response: {other:?}").into()),
    };
    serve_once(hex::encode(document).as_bytes(), port)
}
