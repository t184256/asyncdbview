{
  description = "Limited async-first ORM with a local cache";

  inputs.flake-utils.url = "github:numtide/flake-utils";
  #inputs.nixpkgs.url = "github:NixOS/nixpkgs";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python3Packages = pkgs.python311Packages;  # force 3.11+
        pylama = python3Packages.pylama.overridePythonAttrs (_: {
          # https://github.com/klen/pylama/issues/232
          patches = [
            (pkgs.fetchpatch {
              url = "https://github.com/klen/pylama/pull/233.patch";
              hash = "sha256-jaVG/vuhkPiHEL+28Pf1VuClBVlFtlzDohT0mZasL04=";
            })
          ];
        });
        pytest-asyncio =
          python3Packages.pytest-asyncio.overridePythonAttrs rec {
            pname = "pytest-asyncio";
            version = "0.21.0";  # first one with typing
            src = pkgs.fetchPypi {
              inherit pname version;
              sha256 = "sha256-Kziklq71b1aw6HVX7DE+EeGrknb8OGP2p74PHQ5BXhs=";
            };
          };
        deps = pyPackages: with pyPackages; [
          sqlalchemy aiosqlite
        ];
        tools = pkgs: pyPackages: (with pyPackages; [
          pytest pytestCheckHook
          coverage pytest-cov
          mypy pytest-mypy
          pylama pyflakes pycodestyle pydocstyle mccabe pylint
          eradicate
          pytest-asyncio
        ]);

        asyncdbview = python3Packages.buildPythonPackage {
          pname = "asyncdbview";
          version = "0.0.1";
          src = ./.;
          format = "pyproject";
          propagatedBuildInputs = deps python3Packages;
          nativeBuildInputs = [ python3Packages.setuptools ];
          checkInputs = tools pkgs python3Packages;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = [(python3Packages.python.withPackages deps)];
          nativeBuildInputs = tools pkgs python3Packages;
          shellHook = ''
            export PYTHONASYNCIODEBUG=1 PYTHONWARNINGS=error
          '';
        };
        packages.asyncdbview = asyncdbview;
        packages.default = asyncdbview;
      }
    );
}
