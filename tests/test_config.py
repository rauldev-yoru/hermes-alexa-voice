"""Config precedence: defaults -> config.json -> ALEXA_* env."""
from __future__ import annotations


def test_defaults_without_file_or_env(load_config):
    cfg = load_config()
    assert cfg.PORT == 8099
    assert cfg.BRAND_TITLE == "AGENT"
    assert cfg.SKIP_VERIFY is False
    assert cfg.MEDIA_ROOT is None
    assert cfg.NOTIFY_CFG["title"] == "AGENT"  # falls back to brand_title


def test_file_overrides_defaults(load_config):
    cfg = load_config({"port": 9000, "brand_title": "AOI", "image_roots": ["/a", "/b"]})
    assert cfg.PORT == 9000
    assert cfg.BRAND_TITLE == "AOI"
    assert [str(p) for p in cfg.GALLERY_ROOTS] == ["/a", "/b"]
    assert cfg.NOTIFY_CFG["title"] == "AOI"


def test_env_overrides_file(load_config):
    cfg = load_config({"port": 9000, "brand_title": "AOI"},
                      {"ALEXA_PORT": "9100", "ALEXA_BRAND_TITLE": "ENV"})
    assert cfg.PORT == 9100
    assert cfg.BRAND_TITLE == "ENV"


def test_env_types(load_config):
    cfg = load_config(env={"ALEXA_SKIP_VERIFY": "yes", "ALEXA_AGENT_SYNC_BUDGET": "2.5",
                           "ALEXA_IMAGE_ROOTS": "/x:/y", "ALEXA_MEDIA_ROOT": "null",
                           "ALEXA_PORT": "not-a-number"})
    assert cfg.SKIP_VERIFY is True
    assert cfg.SYNC_BUDGET == 2.5
    assert [str(p) for p in cfg.GALLERY_ROOTS] == ["/x", "/y"]
    assert cfg.MEDIA_ROOT is None
    assert cfg.PORT == 8099  # unparsable int is ignored, not a crash


def test_notify_section_merges_with_defaults(load_config):
    cfg = load_config({"notify": {"quiet_hours": "23-8"}})
    assert cfg.NOTIFY_CFG["quiet_hours"] == "23-8"
    assert cfg.NOTIFY_CFG["dedupe_minutes"] == 5  # untouched default survives


def test_quoted_false_in_file_does_not_disable_verification(load_config):
    # bool("false") is True: this used to switch signature checks OFF.
    cfg = load_config({"skip_verify": "false", "voice_enabled": "no", "chime": "true"})
    assert cfg.SKIP_VERIFY is False
    assert cfg.CFG["voice_enabled"] is False
    assert cfg.CHIME_ENABLED is True


def test_dict_sections_from_env_are_json(load_config):
    # Used to crash at import: the raw string was merged with ** into a dict.
    cfg = load_config(env={"ALEXA_NOTIFY": '{"chime": false}',
                           "ALEXA_STRINGS": '{"done": "Listo."}'})
    assert cfg.NOTIFY_CFG["chime"] is False
    assert cfg.NOTIFY_CFG["dedupe_minutes"] == 5
    assert cfg.s("done") == "Listo."


def test_invalid_dict_env_is_ignored(load_config):
    cfg = load_config(env={"ALEXA_NOTIFY": "chime=false"})
    assert cfg.NOTIFY_CFG["chime"] is True


def test_state_dir_env_beats_file_and_is_created(load_config, tmp_path):
    cfg = load_config({"state_dir": str(tmp_path / "from-file")},
                      {"ALEXA_STATE_DIR": str(tmp_path / "from-env")})
    assert cfg.STATE_DIR == tmp_path / "from-env"
    assert (tmp_path / "from-env" / "cache").is_dir()
    assert not (tmp_path / "from-file").exists()


def test_repo_placeholder_is_expanded(load_config):
    cfg = load_config()
    assert cfg._path("$REPO/state") == cfg.REPO_DIR / "state"
    assert cfg._path("null") is None


def test_strings_override_and_bad_placeholder(load_config):
    cfg = load_config({"strings": {"playing": "Reproduciendo {titulo}."}})
    assert cfg.s("playing", title="x") == "Reproduciendo {titulo}."  # no crash
    assert cfg.s("done") == "Done."


def test_ca_bundle_first_existing_candidate(load_config, tmp_path):
    cfg = load_config()
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    assert cfg._find_ca_bundle(["", str(tmp_path / "missing"), str(bundle)]) == str(bundle)


def test_ca_bundle_falls_back_to_certifi(load_config, tmp_path):
    import certifi

    cfg = load_config()
    assert cfg._find_ca_bundle([str(tmp_path / "missing")]) == certifi.where()


def test_ca_candidates_cover_macos_and_alpine(load_config):
    assert "/etc/ssl/cert.pem" in load_config()._CA_CANDIDATES
