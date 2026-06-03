# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""manyread config + project-local store resolution (stdlib only).

Storage model (v2): everything lives in a VISIBLE, project-local store folder named
`manyread/` (default `./manyread`, chosen at init) — there is NO `~/.manyread` home dir,
so the store travels with the repo and is easy to share.

Store layout:
  manyread/
    manyread.json        SHARED config (alias, languages, exts, ignore)         [commit]
    source.db            index + symbols/edges                                  [commit]
    refs/                SHARED curated refs                                     [commit]
    traces/              SHARED static / durable query patterns                 [commit]
    user/                PER-USER config (machine paths, overrides)             [gitignored]
    short/               SHORT-TERM, version-tagged, NOT archived               [gitignored]
      refs/  rdc/  traces/   ref+rdc copies for this version; dynamic traces
    .gitignore           ignores user/ + short/ (source.db IS committed)

Resolution:
  store  = --store arg > MANYREAD_STORE env > walk up from cwd for a `manyread/` dir
  root   = --root arg > user/config.json "root" > manyread.json "root" > store.parent
  config = manyread.json (shared) overlaid by user/config.json (per-user)

Keep import-safe: NO side effects at import time.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

STORE_DIRNAME = "manyread"

# --- Language -> extension presets (built-in) -------------------------------
LANG_EXTS: dict[str, list[str]] = {
    "cpp": [".h", ".hpp", ".hh", ".inl", ".ipp", ".c", ".cc", ".cpp", ".cxx"],
    "python": [".py", ".pyi"],
    "javascript": [".js", ".jsx", ".mjs", ".cjs"],
    "typescript": [".ts", ".tsx"],
    "csharp": [".cs"],
    "java": [".java"],
    "gdscript": [".gd"],
    "shader": [".hlsl", ".usf", ".ush"],
    "glsl": [".glsl", ".vert", ".frag", ".comp", ".geom", ".tesc", ".tese"],
    "docs": [".md", ".json", ".ini"],
}

DEFAULT_IGNORE_GLOBS: list[str] = [
    "ThirdParty/*",
    "Intermediate/*",
    "*/node_modules/*",
]

# .gitignore written into the store: keep per-user + short-term out of git,
# but SHARE source.db / refs / traces / manyread.json.
STORE_GITIGNORE = "# manyread store — per-user + short-term content is NOT shared\nuser/\nshort/\n"


@dataclass
class ProjectConfig:
    alias: str
    store: Path              # the manyread/ dir
    root: Path               # the source tree being indexed
    db_path: Path            # <store>/source.db
    refs_dir: Path           # <store>/refs        (shared curated refs)
    traces_dir: Path         # <store>/traces      (static / durable traces)
    user_dir: Path           # <store>/user        (per-user, gitignored)
    short_dir: Path          # <store>/short       (ephemeral, gitignored)
    short_refs_dir: Path     # <store>/short/refs  (version-tagged ref copies)
    short_rdc_dir: Path      # <store>/short/rdc   (version-tagged rdc copies)
    short_traces_dir: Path   # <store>/short/traces (dynamic / dated traces)
    languages: list[str]
    exts: list[str]
    profile: str | None
    ignore_globs: list[str] = field(default_factory=list)


# --- store discovery --------------------------------------------------------
def find_store(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) to find a `manyread/` store dir.

    A store is a dir named `manyread` that contains `manyread.json`. Also honors
    the MANYREAD_STORE env override (an explicit store path).
    """
    env = os.environ.get("MANYREAD_STORE")
    if env:
        p = Path(env)
        return p if p.exists() else None
    cur = Path(start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        cand = d / STORE_DIRNAME
        if (cand / "manyread.json").is_file():
            return cand
        # also accept being *inside* the store dir
        if d.name == STORE_DIRNAME and (d / "manyread.json").is_file():
            return d
    return None


def _store_paths(store: Path) -> dict[str, Path]:
    return {
        "db_path": store / "source.db",
        "refs_dir": store / "refs",
        "traces_dir": store / "traces",
        "user_dir": store / "user",
        "short_dir": store / "short",
        "short_refs_dir": store / "short" / "refs",
        "short_rdc_dir": store / "short" / "rdc",
        "short_traces_dir": store / "short" / "traces",
    }


def default_exts_for(languages: list[str]) -> list[str]:
    exts: list[str] = []
    for lang in languages:
        for ext in LANG_EXTS.get(lang.lower(), []):
            if ext not in exts:
                exts.append(ext)
    return exts


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_shared(store: Path) -> dict:
    """The committed, shared config: <store>/manyread.json."""
    return _read_json(store / "manyread.json")


def load_user(store: Path) -> dict:
    """The per-user, gitignored config: <store>/user/config.json (may be absent)."""
    return _read_json(store / "user" / "config.json")


# --- view-hide config (committed, shared, VIEW-LEVEL + RECOVERABLE) ----------
# A symbol view-hide config records ubiquitous high-fan-in noise (int32 / FString /
# TArray / primitives) to HIDE BY DEFAULT in the boundary html. It is NOT the
# destructive enrich `drop` (which deletes from the index) — matched nodes stay in
# the index, stay in DATA, stay LISTED in the hide panel, and are merely applied-
# hidden on load (re-enableable). Home: a committed `view_hide` key inside the
# shared <store>/manyread.json (travels with the repo; auto-discovered each run).
_VIEW_HIDE_KEYS = {"version", "names", "patterns", "min_fan_in"}


def validate_view_hide(vh: dict) -> list[str]:
    """Return a list of human-readable validation errors for a view_hide doc (empty == OK)."""
    if not isinstance(vh, dict):
        return ["view_hide must be an object"]
    errs: list[str] = []
    if vh.get("version", 1) != 1:
        errs.append("view_hide.version must be 1")
    for k in ("names", "patterns"):
        v = vh.get(k)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f"view_hide.{k} must be a list of strings")
    mfi = vh.get("min_fan_in")
    if mfi is not None and (not isinstance(mfi, int) or isinstance(mfi, bool) or mfi < 0):
        errs.append("view_hide.min_fan_in must be an int >= 0")
    return errs


def load_view_hide(store: Path, override_path: Path | None = None) -> dict | None:
    """Resolve the committed symbol view-hide config. ``None`` => behave as v0.6.0.

    Precedence: ``override_path`` (--ignore file) > ``manyread.json['view_hide']`` > None.
    Liberal on read: a --ignore file may be a ``{view_hide:{...}}`` wrapper OR a bare
    ``{names,patterns,min_fan_in}``. Malformed structure => warn to stderr + return None.

    An EXPLICIT --ignore that is missing/unreadable is a HARD, VISIBLE condition (warn
    loudly): the user asked for a file, so silently behaving like v0.6.0 would hide the
    mistake. An ABSENT manyread.json[view_hide] is silent (correct v0.6.0 behavior).
    """
    import sys

    if override_path is not None:
        p = Path(override_path)
        if not p.is_file():
            print(f"manyread: --ignore file not found: {p}", file=sys.stderr)
            return None
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"manyread: --ignore file is not valid JSON ({p}): {exc}", file=sys.stderr)
            return None
        if not isinstance(doc, dict):
            print(f"manyread: --ignore file must be a JSON object: {p}", file=sys.stderr)
            return None
        vh = doc.get("view_hide", doc)          # accept wrapped OR bare
    else:
        mr_json = store / "manyread.json"
        if mr_json.is_file():
            # Distinguish "present but unreadable/empty" (a botched hand-merge) from a
            # clean config with no view_hide key, since the export round-trip invites
            # hand-editing manyread.json: a syntax error silently resets ALL shared config.
            shared = _read_json(mr_json)
            if not shared:
                print(f"manyread: {mr_json} present but unreadable/empty — "
                      "shared config (incl. view_hide) ignored", file=sys.stderr)
                return None
            vh = shared.get("view_hide")
        else:
            vh = None
    if not vh or not isinstance(vh, dict):
        return None
    errs = validate_view_hide(vh)
    if errs:
        print("manyread: ignoring malformed view_hide config: " + "; ".join(errs), file=sys.stderr)
        return None
    unknown = sorted(set(vh) - _VIEW_HIDE_KEYS)
    if unknown:
        print("manyread: view_hide has unknown key(s) " + ", ".join(unknown)
              + " (known: version/names/patterns/min_fan_in) — proceeding", file=sys.stderr)
    return vh


def init_store(location: Path, alias: str | None = None,
               languages: list[str] | None = None, exts: list[str] | None = None,
               ignore_globs: list[str] | None = None, root: Path | None = None) -> Path:
    """Create a fresh `manyread/` store under `location` (default: cwd).

    Writes manyread.json (shared) + the subdir skeleton + .gitignore. Returns the
    store path. Idempotent: existing files are not overwritten.
    """
    store = Path(location).resolve() / STORE_DIRNAME
    paths = _store_paths(store)
    for d in (store, paths["refs_dir"], paths["traces_dir"], paths["user_dir"],
              paths["short_dir"], paths["short_refs_dir"], paths["short_rdc_dir"],
              paths["short_traces_dir"]):
        d.mkdir(parents=True, exist_ok=True)

    cfg_file = store / "manyread.json"
    if not cfg_file.exists():
        langs = languages or []
        payload = {
            "alias": alias or Path(location).resolve().name,
            "languages": langs,
            "exts": exts if exts is not None else default_exts_for(langs),
            "profile": None,
            "ignore_globs": ignore_globs if ignore_globs is not None else list(DEFAULT_IGNORE_GLOBS),
        }
        # A source root different from the store's parent is per-user, not shared;
        # only record it here if explicitly provided as a shared default.
        if root is not None:
            payload["root"] = str(root)
        cfg_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    gi = store / ".gitignore"
    if not gi.exists():
        gi.write_text(STORE_GITIGNORE, encoding="utf-8")

    return store


def save_user(store: Path, data: dict) -> None:
    """Write the per-user config (machine paths / overrides) — gitignored."""
    user_cfg = store / "user" / "config.json"
    user_cfg.parent.mkdir(parents=True, exist_ok=True)
    user_cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def resolve_project(root: str | None = None, store: str | None = None) -> ProjectConfig:
    """Resolve a ProjectConfig from a store + source root.

    store: explicit --store > MANYREAD_STORE env > discovered by walking up from
    `root` (if given) or cwd. Raises SystemError with guidance when none is found.
    root:  explicit --root > user config "root" > shared config "root" > store.parent.
    """
    store_path: Path | None
    if store:
        store_path = Path(store).resolve()
        if not (store_path / "manyread.json").is_file() and store_path.name != STORE_DIRNAME:
            # allow pointing --store at a project dir that contains manyread/
            cand = store_path / STORE_DIRNAME
            if (cand / "manyread.json").is_file():
                store_path = cand
    else:
        start = Path(root).resolve() if root else None
        store_path = find_store(start)

    if store_path is None:
        raise SystemError(
            "no manyread store found. Run mr-init to create one "
            "(default ./manyread), or pass --store PATH / set MANYREAD_STORE."
        )
    store_path = store_path.resolve()

    shared = load_shared(store_path)
    user = load_user(store_path)
    paths = _store_paths(store_path)

    # source root: --root > user > shared > store.parent
    root_val = root or user.get("root") or shared.get("root")
    root_path = Path(root_val).resolve() if root_val else store_path.parent

    alias = user.get("alias") or shared.get("alias") or store_path.parent.name
    languages = user.get("languages") or shared.get("languages") or []
    exts = user.get("exts") or shared.get("exts") or default_exts_for(languages)
    profile = user.get("profile", shared.get("profile"))
    ignore_globs = user.get("ignore_globs")
    if ignore_globs is None:
        ignore_globs = shared.get("ignore_globs")
    if ignore_globs is None:
        ignore_globs = list(DEFAULT_IGNORE_GLOBS)

    return ProjectConfig(
        alias=alias,
        store=store_path,
        root=root_path,
        db_path=paths["db_path"],
        refs_dir=paths["refs_dir"],
        traces_dir=paths["traces_dir"],
        user_dir=paths["user_dir"],
        short_dir=paths["short_dir"],
        short_refs_dir=paths["short_refs_dir"],
        short_rdc_dir=paths["short_rdc_dir"],
        short_traces_dir=paths["short_traces_dir"],
        languages=list(languages),
        exts=list(exts),
        profile=profile,
        ignore_globs=list(ignore_globs),
    )


# --- per-user hub (env dir): a registry of activated store paths ------------
# The hub is intentionally just a LIST of where manyread is active (for the user
# / Claude to browse, delete, and find reusable stores) — NOT the indexed data,
# which stays project-local in each store. The hub may live in the env/home dir
# because it holds only paths.
def hub_dir() -> Path:
    """Per-user hub dir: MANYREAD_HOME env, else ~/.manyread."""
    env = os.environ.get("MANYREAD_HOME")
    return Path(env) if env else (Path.home() / ".manyread")


def _hub_registry_path() -> Path:
    return hub_dir() / "stores.json"


def list_stores() -> dict:
    """Return the hub registry: { "<store abs path>": {alias, root, updated} }."""
    p = _hub_registry_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def register_store(store: Path, alias: str | None = None, root: Path | None = None) -> None:
    """Record (or refresh) a store in the per-user hub registry."""
    reg = list_stores()
    reg[str(Path(store).resolve())] = {
        "alias": alias or "",
        "root": str(Path(root).resolve()) if root else "",
        "updated": int(time.time()),
    }
    p = _hub_registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


def unregister_store(store: Path) -> bool:
    """Remove a store from the hub registry (does NOT touch the store on disk)."""
    reg = list_stores()
    key = str(Path(store).resolve())
    if key in reg:
        del reg[key]
        _hub_registry_path().write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
        return True
    return False


# --- system-location guard --------------------------------------------------
def is_system_location(path: Path) -> bool:
    """True when `path` is an unsafe place to drop a store / index in place.

    Covers drive roots (C:\\, W:\\, /), the user home, and common shell folders
    directly under home (Desktop/Documents/Downloads/OneDrive). The agent should
    offer a dedicated project subfolder instead of indexing these directly.
    """
    p = Path(path).resolve()
    if p.parent == p:  # filesystem / drive root
        return True
    home = Path.home().resolve()
    if p == home:
        return True
    if p.parent == home and p.name.lower() in {
        "desktop", "documents", "downloads", "onedrive",
    }:
        return True
    return False
