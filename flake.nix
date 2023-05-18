{
  description = "Limited async-first ORM with a local cache";

  inputs.flake-utils.url = "github:numtide/flake-utils";
  #inputs.nixpkgs.url = "github:NixOS/nixpkgs";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        deps = pyPackages: with pyPackages; [
          sqlalchemy aiosqlite
        ];
        tools = pyPackages: (with pyPackages; [
          pytest pytestCheckHook
          coverage pytest-cov
          mypy pytest-mypy
          (pylama' pyPackages)
          pyflakes pycodestyle pydocstyle mccabe pylint
          eradicate
          (pytest-asyncio' pyPackages)
        ]);

        pylama' = pyPackages:
          pyPackages.pylama.overridePythonAttrs (_: {
            # https://github.com/klen/pylama/issues/232
            patches = [
              (pkgs.fetchpatch {
                url = "https://github.com/klen/pylama/pull/233.patch";
                hash = "sha256-jaVG/vuhkPiHEL+28Pf1VuClBVlFtlzDohT0mZasL04=";
              })
            ];
          });
        pytest-asyncio' = pyPackages:
          pyPackages.pytest-asyncio.overridePythonAttrs rec {
            pname = "pytest-asyncio";
            version = "0.21.0";  # first one with typing
            src = pyPackages.fetchPypi {
              inherit pname version;
              sha256 = "sha256-Kziklq71b1aw6HVX7DE+EeGrknb8OGP2p74PHQ5BXhs=";
            };
          };

        asyncdbview-package = {python3Packages}:
          python3Packages.buildPythonPackage {
            pname = "asyncdbview";
            version = "0.0.1";
            src = ./.;
            format = "pyproject";
            propagatedBuildInputs = deps python3Packages;
            nativeBuildInputs = [ python3Packages.setuptools ];
            checkInputs = tools python3Packages;
          };
        asyncdbview-pyextension = pyFinal: pyPrev: {
          asyncdbview = pkgs.callPackage asyncdbview-package {
            python3Packages = pyFinal;
          };
        };

        overlay = final: prev: {
          pythonPackagesExtensions =
            prev.pythonPackagesExtensions ++ [ asyncdbview-pyextension ];
        };
        pkgs = import nixpkgs { inherit system; overlays = [ overlay ]; };

        defaultPython3Packages = pkgs.python311Packages;  # 3.11+
        asyncdbview = pkgs.callPackage asyncdbview-package {
          python3Packages = defaultPython3Packages;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          buildInputs = [(defaultPython3Packages.python.withPackages deps)];
          nativeBuildInputs = tools defaultPython3Packages;
          shellHook = ''
            export PYTHONASYNCIODEBUG=1 PYTHONWARNINGS=error
          '';
        };
        packages.asyncdbview = asyncdbview;
        packages.default = asyncdbview;
        overlays.default = overlay;
      }
    );
}
