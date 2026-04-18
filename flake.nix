{
  description = "SolRL reproducible AWS Nitro worker EIF";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/d3cf0dc441e013241cbf342120c304e90c9e58a6";
    nitro-util.url = "github:monzo/aws-nitro-util/96f3bb204536dce32882a7e4affd6e8cea828b48";
    oyster.url = "github:marlinprotocol/oyster-monorepo/f60874a27f56eee2974cf0099ff80e9627c8f1dd";
  };

  outputs = {
    self,
    nixpkgs,
    nitro-util,
    oyster,
  }: let
    system = "x86_64-linux";
    pkgs = nixpkgs.legacyPackages.${system};
    nitro = nitro-util.lib.${system};
    oysterPkgs = oyster.packages.${system}.gnu;
    worker = pkgs.rustPlatform.buildRustPackage {
      pname = "solrl-nitro-worker";
      version = "0.1.0";
      src = ./crates/solrl-nitro-worker;
      cargoLock.lockFile = ./crates/solrl-nitro-worker/Cargo.lock;
      doCheck = false;
    };
    app = pkgs.runCommand "solrl-nitro-worker-root" {} ''
      mkdir -p $out/app
      cp ${worker}/bin/solrl-nitro-worker $out/app/solrl-nitro-worker
    '';
    initPerms = pkgs.runCommand "solrl-nitro-init" {} ''
      cp ${oysterPkgs.kernels.vanilla.init} $out
      chmod +x $out
    '';
  in {
    packages.${system} = {
      solrl-nitro-worker = worker;
      solrl-nitro-worker-eif = nitro.buildEif {
        name = "solrl-nitro-worker";
        arch = "x86_64";
        init = initPerms;
        kernel = oysterPkgs.kernels.vanilla.kernel;
        kernelConfig = oysterPkgs.kernels.vanilla.kernelConfig;
        nsmKo = oysterPkgs.kernels.vanilla.nsmKo;
        cmdline = builtins.readFile nitro.blobs.x86_64.cmdLine;
        entrypoint = "/app/solrl-nitro-worker";
        env = "SOLRL_VSOCK_PORT=5005";
        copyToRoot = pkgs.buildEnv {
          name = "solrl-nitro-worker-image-root";
          paths = [app pkgs.busybox];
          pathsToLink = ["/app" "/bin"];
        };
      };
      default = self.packages.${system}.solrl-nitro-worker-eif;
    };
  };
}
