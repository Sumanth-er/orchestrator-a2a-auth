from pydantic_settings import BaseSettings, SettingsConfigDict


class KeycloakSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kc_url: str = "http://localhost:8080"
    kc_realm: str = "a2a-demo"

    @property
    def issuer(self) -> str:
        return f"{self.kc_url}/realms/{self.kc_realm}"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/certs"

    @property
    def token_endpoint(self) -> str:
        return f"{self.issuer}/protocol/openid-connect/token"
