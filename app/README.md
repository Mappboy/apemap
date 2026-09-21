# APE Map Dash Application (Deprecated)

> [!WARNING]
> **Deprecation Notice**: This Dash prototype application is deprecated and no longer actively maintained. It has been superseded by the static results page and modern interactive visualizations published on [cpoole.dev](https://cpoole.dev).

## Historical Usage

Historically, this Plotly Dash dashboard was developed to explore Australian Parliamentarian education backgrounds, political party representation, and school funding statistics interactively.

### Running the Legacy App

The application expects to be run with `app/` as its working directory and reads spatial data from `../data/aped.gpkg`:

```bash
cd app
python app.py
```

Environment variables may optionally be supplied via `../.env` for PostgreSQL mode (`USE_POSTGRES = True`), though the default configuration runs against the local GeoPackage with SQLite.
