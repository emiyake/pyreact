def run_web(app_component_fn, *, host="127.0.0.1", port=8000, reload=False, **uvicorn_kwargs):
    import uvicorn
    from pyreact.web.server import create_fastapi_app
    app, _ = create_fastapi_app(app_component_fn)
    uvicorn.run(app, host=host, port=port, reload=reload, **uvicorn_kwargs)