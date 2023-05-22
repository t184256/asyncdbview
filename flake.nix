{
  description = "Limited async-first ORM with a local cache";

  outputs = { self, nixpkgs, flake-utils }:
    let
      deps = pyPackages: with pyPackages; [
        sqlalchemy aiosqlite
      ];
      tools = pkgs: pyPackages: (with pyPackages; [
        pytest pytestCheckHook
        coverage pytest-cov
        mypy pytest-mypy
        (pkgs.callPackage pylama' { python3Packages = pyPackages; })
        pyflakes pycodestyle pydocstyle mccabe pylint
        eradicate
        (pkgs.callPackage pytest-asyncio' { python3Packages = pyPackages; })
      ]);

      pylama' = {python3Packages, fetchpatch}:
        python3Packages.pylama.overridePythonAttrs (_: {
          # https://github.com/klen/pylama/issues/232
          patches = [
            (fetchpatch {
              url = "https://github.com/klen/pylama/pull/233.patch";
              hash = "sha256-jaVG/vuhkPiHEL+28Pf1VuClBVlFtlzDohT0mZasL04=";
            })
          ];
        });
      pytest-asyncio' = {python3Packages}:
        python3Packages.pytest-asyncio.overridePythonAttrs rec {
          pname = "pytest-asyncio";
          version = "0.21.0";  # first one with typing
          src = python3Packages.fetchPypi {
            inherit pname version;
            sha256 = "sha256-Kziklq71b1aw6HVX7DE+EeGrknb8OGP2p74PHQ5BXhs=";
          };
        };
      asyncdbview-package = {python3Packages, pkgs}:
        python3Packages.buildPythonPackage {
          postPatch = "set -xv";
          pname = "asyncdbview";
          version = "0.0.1";
          src = ./.;
          format = "pyproject";
          propagatedBuildInputs = deps python3Packages;
          nativeBuildInputs = [ python3Packages.setuptools ];
          checkInputs = tools pkgs python3Packages;
        };
      overlay = final: prev: {
        pythonPackagesExtensions =
          prev.pythonPackagesExtensions ++ [(pyFinal: pyPrev: {
            asyncdbview = final.callPackage asyncdbview-package {
              python3Packages = pyFinal;
            };
          })];
      };
    in
      flake-utils.lib.eachDefaultSystem (system:
        let
          pkgs = import nixpkgs { inherit system; overlays = [ overlay ]; };
          defaultPython3Packages = pkgs.python311Packages;  # 3.11+
          asyncdbview = pkgs.callPackage asyncdbview-package {
            python3Packages = defaultPython3Packages;
          };
        in
        {
          devShells.default = pkgs.mkShell {
            buildInputs = [(defaultPython3Packages.python.withPackages deps)];
            nativeBuildInputs = tools pkgs defaultPython3Packages;
            shellHook = ''
              export PYTHONASYNCIODEBUG=1 PYTHONWARNINGS=error
            '';
          };
          packages.asyncdbview = asyncdbview;
          packages.default = asyncdbview;
        }
    ) // { overlays.default = overlay; };
}
