"""Configuration de l'application, entierement pilotee par variables d'environnement."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ALLBUDY_", env_file=".env", extra="ignore", case_sensitive=False
    )

    # --- Chemins ---
    data_dir: Path = Path("./data")
    """Racine des donnees persistees (base, fichiers, miniatures)."""

    # --- Serveur HTTP ---
    host: str = "0.0.0.0"
    port: int = 8088
    base_path: str = ""
    """Prefixe si l'app est servie derriere un reverse proxy (ex: /allbudy)."""

    # --- Securite ---
    secret_key: str = Field(default="")
    """Cle de signature JWT + chiffrement des mots de passe FTP/SFTP.

    Generee et persistee dans data_dir/secret.key si absente.
    """
    session_ttl_hours: int = 24 * 14
    auth_enabled: bool = False
    """Desactive par defaut: AllBudy est concu pour un reseau local deja
    cloisonne. Activez-le (et protegez l'acces reseau en consequence) si
    l'instance est exposee au-dela de ce reseau; l'interface fournie n'a
    plus d'ecran de connexion, l'activer suppose de gerer l'authentification
    autrement (reverse proxy, jeton passe a la main)."""
    admin_username: str = "admin"
    admin_password: str = "allbudy"
    """Identifiants du compte cree au premier demarrage uniquement."""

    # --- Parc d'imprimantes ---
    poll_interval: float = 2.0
    """Intervalle (s) de rafraichissement de l'etat d'une imprimante."""
    reconnect_delay: float = 5.0
    connect_timeout: float = 6.0
    scheduler_interval: float = 5.0
    """Intervalle (s) du dispatcher de la file d'attente."""

    # --- Fichiers ---
    max_upload_mb: int = 512
    keep_thumbnails: bool = True

    # --- Reseau / decouverte ---
    discovery_ports: str = "7125,9999"
    discovery_timeout: float = 1.0
    discovery_concurrency: int = 128

    # --- Divers ---
    log_level: str = "INFO"
    demo_printers: int = 0
    """Nombre d'imprimantes simulees creees au premier demarrage (demo/tests)."""

    @field_validator("base_path")
    @classmethod
    def _strip_base_path(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not v.startswith("/"):
            v = "/" + v
        return v

    @property
    def db_path(self) -> Path:
        return self.data_dir / "allbudy.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbnails"

    @property
    def discovery_port_list(self) -> list[int]:
        out: list[int] = []
        for chunk in self.discovery_ports.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.append(int(chunk))
        return out

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.files_dir, self.thumbs_dir):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_secret_key(self) -> str:
        """Retourne la cle secrete, en la generant/persistant au besoin."""
        if self.secret_key:
            return self.secret_key
        self.ensure_dirs()
        key_file = self.data_dir / "secret.key"
        if key_file.exists():
            self.secret_key = key_file.read_text(encoding="utf-8").strip()
        else:
            self.secret_key = secrets.token_urlsafe(48)
            key_file.write_text(self.secret_key, encoding="utf-8")
            key_file.chmod(0o600)
        return self.secret_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    settings.resolve_secret_key()
    return settings
