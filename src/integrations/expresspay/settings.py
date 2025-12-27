from pydantic import BaseModel, Field


class ExpressPaySettings(BaseModel):
    token: str = Field(..., description="API token Express Pay (обязателен)")
    account_number: str = Field(..., description="Базовый номер аккаунта Express Pay")
    secret_word: str = Field("", description="Secret word для HMAC подписи (может быть пустым)")
    use_signature: bool = Field(True, description="Добавлять ли signature в запросы")
    sandbox: bool = Field(False, description="Использовать sandbox-api.express-pay.by")
    timeout: float = Field(15.0, ge=1.0, le=60.0)

    @property
    def api_base_url(self) -> str:
        return "https://sandbox-api.express-pay.by" if self.sandbox else "https://api.express-pay.by"
