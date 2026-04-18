FROM nixos/nix:latest

COPY docker/nix-builder-entrypoint.sh /usr/local/bin/nix-builder-entrypoint.sh
RUN chmod +x /usr/local/bin/nix-builder-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/nix-builder-entrypoint.sh"]
CMD ["nix", "--version"]
