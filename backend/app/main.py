from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Knowledge API")

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
