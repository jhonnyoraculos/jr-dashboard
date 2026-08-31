from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone

import pandas as pd


STREAMLIT_APP_MODULE = "streamlit_app"


def _running_as_streamlit_entrypoint() -> bool:
    if __name__ != "__main__":
        return False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
    except Exception:
        return False
    return get_script_run_ctx() is not None


if _running_as_streamlit_entrypoint():
    os.environ.setdefault("JR_SKIP_WARM_CACHE", "1")
    from streamlit_app import main as streamlit_main
    import streamlit as st

    streamlit_main()
    st.stop()


DB_TABLES = {
    "combustivel": "dashboard_combustivel",
    "combustivel_km": "dashboard_combustivel_km",
    "empilhadeira_horas": "dashboard_empilhadeira_horas",
    "combustiveis": "dashboard_combustiveis",
    "postos": "dashboard_postos",
    "manutencao": "dashboard_manutencao",
    "pneus": "dashboard_pneus",
    "hoteis": "dashboard_hoteis",
    "pedagio": "dashboard_pedagio",
    "peso": "dashboard_peso",
    "rodagem_rota": "dashboard_rodagem_rota",
    "placas": "dashboard_placas",
    "salarios_transporte": "dashboard_salarios_transporte",
}
DB_METADATA_TABLE = "dashboard_metadata"
BACKUP_METADATA_KEY = "backup.last_downloaded_at"
_DB_ENGINE = None
_METADATA_CACHE_SECONDS = float(os.environ.get("JR_METADATA_CACHE_SECONDS", "30") or 30)
_METADATA_CACHE = {"loaded": False, "loaded_at": 0.0, "values": {}, "lock": threading.Lock()}

_PEDAGIO_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_COMBUSTIVEL_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_COMBUSTIVEL_KM_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_EMPILHADEIRA_HORAS_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_MANUTENCAO_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_PNEUS_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_HOTEIS_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_PESO_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_RODAGEM_ROTA_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_OVERVIEW_CACHE = {"mtimes": None, "dados": None}
_PLATE_REGISTRY_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_PLACAS_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_SALARIOS_TRANSPORTE_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_TEXT_REGISTRY_CACHES = {
    "combustiveis": {"mtime": None, "df": None, "lock": threading.Lock()},
    "postos": {"mtime": None, "df": None, "lock": threading.Lock()},
}
_CACHE_MAP = {
    "combustivel": _COMBUSTIVEL_CACHE,
    "combustivel_km": _COMBUSTIVEL_KM_CACHE,
    "empilhadeira_horas": _EMPILHADEIRA_HORAS_CACHE,
    "manutencao": _MANUTENCAO_CACHE,
    "pneus": _PNEUS_CACHE,
    "hoteis": _HOTEIS_CACHE,
    "pedagio": _PEDAGIO_CACHE,
    "peso": _PESO_CACHE,
    "rodagem_rota": _RODAGEM_ROTA_CACHE,
    "placas": _PLACAS_CACHE,
    "salarios_transporte": _SALARIOS_TRANSPORTE_CACHE,
}

_COMBUSTIVEL_COLUMNS = [
    "Data",
    "Mes",
    "Km Rodados",
    "Litros",
    "Custo",
    "Combustivel",
    "POSTOS",
    "PLACA",
    "Categoria",
]
_COMBUSTIVEL_KM_COLUMNS = ["Mes", "PLACA", "Km Rodados"]
_EMPILHADEIRA_HORAS_COLUMNS = ["Mes", "PLACA", "Horas"]
_COMBUSTIVEIS_COLUMNS = ["Combustivel"]
_POSTOS_COLUMNS = ["POSTOS"]
_MANUTENCAO_COLUMNS = ["Data", "Mes", "Custo", "PLACA", "OFICINA", "Categoria"]
_PNEUS_COLUMNS = ["Data", "Mes", "PLACA", "Categoria", "Fornecedor", "Quantidade", "Medida", "Custo", "Observacao"]
_HOTEIS_COLUMNS = [
    "Data",
    "Valor",
    "Dias",
    "Mes",
    "Motorista",
    "Ajudante",
    "Cidade",
    "Hotel",
    "Tipo",
    "Categoria",
]
_PEDAGIO_COLUMNS = ["PLACA", "Tipo", "Custo", "Mes", "Data", "Categoria"]
_PESO_COLUMNS = ["Data", "Mes", "Cidade", "Rota", "Peso", "Valor", "PLACA", "Categoria"]
_RODAGEM_ROTA_COLUMNS = ["Mes", "Rota", "PLACA", "Km Rodados"]
_PLACAS_COLUMNS = ["PLACA", "Categoria", "Diaria"]
_SALARIOS_TRANSPORTE_COLUMNS = ["Mes", "Valor"]
_PLATE_ALIASES = {
    "EUX6525": "EUX6F25",
}
_DATASET_COLUMNS = {
    "combustivel": _COMBUSTIVEL_COLUMNS,
    "combustivel_km": _COMBUSTIVEL_KM_COLUMNS,
    "empilhadeira_horas": _EMPILHADEIRA_HORAS_COLUMNS,
    "combustiveis": _COMBUSTIVEIS_COLUMNS,
    "postos": _POSTOS_COLUMNS,
    "manutencao": _MANUTENCAO_COLUMNS,
    "pneus": _PNEUS_COLUMNS,
    "hoteis": _HOTEIS_COLUMNS,
    "pedagio": _PEDAGIO_COLUMNS,
    "peso": _PESO_COLUMNS,
    "rodagem_rota": _RODAGEM_ROTA_COLUMNS,
    "placas": _PLACAS_COLUMNS,
    "salarios_transporte": _SALARIOS_TRANSPORTE_COLUMNS,
}
_COLUMN_SQL_TYPES = {
    "Data": "TIMESTAMP",
    "Mes": "TEXT",
    "Km Rodados": "DOUBLE PRECISION",
    "Horas": "DOUBLE PRECISION",
    "Litros": "DOUBLE PRECISION",
    "Custo": "DOUBLE PRECISION",
    "Combustivel": "TEXT",
    "POSTOS": "TEXT",
    "PLACA": "TEXT",
    "Categoria": "TEXT",
    "Diaria": "DOUBLE PRECISION",
    "Valor": "DOUBLE PRECISION",
    "Dias": "DOUBLE PRECISION",
    "Motorista": "TEXT",
    "Ajudante": "TEXT",
    "Cidade": "TEXT",
    "Rota": "TEXT",
    "Hotel": "TEXT",
    "Tipo": "TEXT",
    "OFICINA": "TEXT",
    "Peso": "DOUBLE PRECISION",
    "Fornecedor": "TEXT",
    "Quantidade": "DOUBLE PRECISION",
    "Medida": "TEXT",
    "Observacao": "TEXT",
}


def _database_url() -> str | None:
    for key in ("DATABASE_URL", "NEON_DATABASE_URL"):
        value = os.environ.get(key)
        if value:
            return value.strip()
    try:
        import streamlit as st

        for key in ("DATABASE_URL", "NEON_DATABASE_URL"):
            value = st.secrets.get(key)
            if value:
                return str(value).strip()
    except Exception:
        pass
    return None


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return f"postgresql+psycopg://{url[len('postgresql://'):]}"
    if url.startswith("postgres://"):
        return f"postgresql+psycopg://{url[len('postgres://'):]}"
    return url


def _db_engine():
    global _DB_ENGINE
    url = _database_url()
    if not url:
        raise RuntimeError("DATABASE_URL/NEON_DATABASE_URL nao configurada. Configure o Secret do Neon no Streamlit.")
    if _DB_ENGINE is None:
        from sqlalchemy import create_engine

        _DB_ENGINE = create_engine(_normalize_database_url(url), pool_pre_ping=True)
    return _DB_ENGINE


def _metadata_table_values(*, force: bool = False) -> dict:
    now = time.monotonic()
    with _METADATA_CACHE["lock"]:
        loaded = bool(_METADATA_CACHE["loaded"])
        loaded_at = float(_METADATA_CACHE["loaded_at"] or 0.0)
        if loaded and not force and now - loaded_at < _METADATA_CACHE_SECONDS:
            return dict(_METADATA_CACHE["values"])

    try:
        from sqlalchemy import text

        query = text(f'SELECT "key", value_json FROM "{DB_METADATA_TABLE}"')
        rows = pd.read_sql_query(query, _db_engine())
    except Exception:
        with _METADATA_CACHE["lock"]:
            if _METADATA_CACHE["loaded"]:
                return dict(_METADATA_CACHE["values"])
            _METADATA_CACHE["loaded"] = True
            _METADATA_CACHE["loaded_at"] = time.monotonic()
            _METADATA_CACHE["values"] = {}
        return {}

    values = {}
    for _, row in rows.iterrows():
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        try:
            values[key] = json.loads(row.get("value_json"))
        except Exception:
            continue

    with _METADATA_CACHE["lock"]:
        _METADATA_CACHE["loaded"] = True
        _METADATA_CACHE["loaded_at"] = time.monotonic()
        _METADATA_CACHE["values"] = values
    return dict(values)


def _update_metadata_cache(key: str, value) -> None:
    with _METADATA_CACHE["lock"]:
        if not _METADATA_CACHE["loaded"]:
            return
        _METADATA_CACHE["values"][key] = value
        _METADATA_CACHE["loaded_at"] = time.monotonic()


def _db_metadata(key: str, default=None):
    return _metadata_table_values().get(key, default)


def _db_version(dataset: str):
    metadata = _metadata_table_values()
    return metadata.get(f"{dataset}.version", metadata.get("import.version", "database"))


def dashboard_data_version(datasets: list[str] | tuple[str, ...] | None = None) -> tuple[tuple[str, str], ...]:
    metadata = _metadata_table_values()
    selected = tuple(datasets or DB_TABLES.keys())
    fallback = metadata.get("import.version", "database")
    return tuple(
        (dataset, str(metadata.get(f"{dataset}.version", fallback)))
        for dataset in selected
    )


def get_backup_status() -> dict:
    raw_value = _db_metadata(BACKUP_METADATA_KEY)
    downloaded_at = None
    if raw_value:
        try:
            downloaded_at = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except Exception:
            downloaded_at = None
    return {"last_downloaded_at": raw_value, "last_downloaded_datetime": downloaded_at}


def mark_backup_downloaded() -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    engine = _db_engine()
    with engine.begin() as conn:
        _write_metadata(conn, BACKUP_METADATA_KEY, timestamp)
    return timestamp


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _read_database_table(dataset: str, columns: list[str], *, date_columns: list[str] | None = None) -> pd.DataFrame:
    table = DB_TABLES[dataset]
    try:
        from sqlalchemy import text

        engine = _db_engine()
        with engine.begin() as conn:
            _ensure_dataset_table(conn, dataset)
        df = pd.read_sql_query(text(f'SELECT * FROM "{table}"'), engine)
    except Exception as exc:
        raise RuntimeError(f'Nao foi possivel ler a tabela "{table}" no Neon.') from exc

    for column in columns:
        if column not in df.columns:
            df[column] = pd.NA
    if date_columns:
        for column in date_columns:
            if column in df.columns:
                df[column] = pd.to_datetime(df[column], errors="coerce")

    df = df[columns + [column for column in df.columns if column not in columns]]
    df.attrs["anos_sheets"] = _db_metadata(f"{dataset}.anos_sheets", [])
    return df.copy()


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _bind_columns(columns: list[str], row: dict, prefix: str) -> tuple[dict[str, str], dict[str, object]]:
    refs: dict[str, str] = {}
    params: dict[str, object] = {}
    for index, column in enumerate(columns):
        bind_name = f"{prefix}_{index}"
        refs[column] = f":{bind_name}"
        params[bind_name] = row.get(column)
    return refs, params


def _metadata_value(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _write_metadata(conn, key: str, value) -> None:
    from sqlalchemy import text

    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {_quote_identifier(DB_METADATA_TABLE)} (
                "key" TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    conn.execute(
        text(
            f"""
            INSERT INTO {_quote_identifier(DB_METADATA_TABLE)} ("key", value_json, updated_at)
            VALUES (:key, :value_json, CURRENT_TIMESTAMP)
            ON CONFLICT (key)
            DO UPDATE SET value_json = EXCLUDED.value_json, updated_at = CURRENT_TIMESTAMP
            """
        ),
        {"key": key, "value_json": _metadata_value(value)},
    )
    _update_metadata_cache(key, value)


def _clear_dataset_cache(dataset: str) -> None:
    if dataset in {
        "placas",
        "combustivel",
        "combustivel_km",
        "empilhadeira_horas",
        "manutencao",
        "pneus",
        "pedagio",
        "peso",
        "rodagem_rota",
    }:
        _PLATE_REGISTRY_CACHE["mtime"] = None
        _PLATE_REGISTRY_CACHE["df"] = None
        _PLACAS_CACHE["mtime"] = None
        _PLACAS_CACHE["df"] = None
    if dataset in {"combustivel", "combustiveis"}:
        _TEXT_REGISTRY_CACHES["combustiveis"]["mtime"] = None
        _TEXT_REGISTRY_CACHES["combustiveis"]["df"] = None
    if dataset in {"combustivel", "postos"}:
        _TEXT_REGISTRY_CACHES["postos"]["mtime"] = None
        _TEXT_REGISTRY_CACHES["postos"]["df"] = None

    if dataset == "combustivel_km":
        targets = ["combustivel", "combustivel_km"]
    elif dataset == "empilhadeira_horas":
        targets = ["empilhadeira_horas"]
    elif dataset == "manutencao":
        targets = ["manutencao", "pneus"]
    elif dataset == "placas":
        targets = ["combustivel", "combustivel_km", "empilhadeira_horas", "manutencao", "pneus", "pedagio", "peso", "rodagem_rota"]
    elif dataset in {"combustiveis", "postos"}:
        targets = ["combustivel"]
    else:
        targets = [dataset]
    for target in targets:
        cache = _CACHE_MAP.get(target)
        if not cache:
            continue
        cache["mtime"] = None
        cache["df"] = None
        if target == "combustivel":
            cache["km_rodados_mensal"] = None
    _OVERVIEW_CACHE["mtimes"] = None
    _OVERVIEW_CACHE["dados"] = None


def _normalize_insert_value(value):
    if isinstance(value, str) and value.strip() in ("", "Todos"):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _prepare_insert_row(dataset: str, row: dict) -> dict:
    columns = _DATASET_COLUMNS[dataset]
    prepared = {column: _normalize_insert_value(row.get(column)) for column in columns}

    if prepared.get("Data") is not None and not prepared.get("Mes"):
        dt = pd.to_datetime(prepared["Data"], errors="coerce")
        if pd.notna(dt):
            prepared["Mes"] = dt.to_period("M").strftime("%Y-%m")

    if "PLACA" in prepared:
        prepared["PLACA"] = _normalize_insert_value(_normalize_plate_value(prepared["PLACA"]))
        if dataset == "placas" and not _is_plate_or_asset_identifier(prepared["PLACA"]):
            prepared["PLACA"] = None
    if "Tipo" in prepared and dataset == "pedagio":
        prepared["Tipo"] = _normalize_tipo_value(prepared["Tipo"])
    if "Combustivel" in prepared:
        prepared["Combustivel"] = _normalize_combustivel_value(prepared["Combustivel"])
    if "Categoria" in prepared:
        prepared["Categoria"] = _normalize_category_value(prepared["Categoria"])
    if "Diaria" in prepared:
        diaria = pd.to_numeric(pd.Series([prepared["Diaria"]]), errors="coerce").iloc[0]
        prepared["Diaria"] = max(float(diaria), 0.0) if pd.notna(diaria) else 0.0
    if prepared.get("PLACA") and _is_forklift_identifier(prepared["PLACA"]):
        prepared["Categoria"] = "Empilhadeira"
    elif prepared.get("PLACA") and _is_equipment_identifier(prepared["PLACA"]):
        prepared["Categoria"] = "Equipamento"

    for column, value in list(prepared.items()):
        if isinstance(value, str):
            value = value.strip()
            prepared[column] = value or None
    return prepared


def _ensure_dataset_table(conn, dataset: str) -> None:
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        return

    from sqlalchemy import text

    create_sql = {
        "placas": f"""
            CREATE TABLE IF NOT EXISTS {_quote_identifier(DB_TABLES["placas"])} (
                "PLACA" TEXT PRIMARY KEY,
                "Categoria" TEXT NOT NULL,
                "Diaria" DOUBLE PRECISION
            )
            """,
        "combustiveis": f"""
            CREATE TABLE IF NOT EXISTS {_quote_identifier(DB_TABLES["combustiveis"])} (
                "Combustivel" TEXT PRIMARY KEY
            )
            """,
        "postos": f"""
            CREATE TABLE IF NOT EXISTS {_quote_identifier(DB_TABLES["postos"])} (
                "POSTOS" TEXT PRIMARY KEY
            )
            """,
    }.get(dataset) or f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(DB_TABLES[dataset])} (
            "__jr_schema_marker" TEXT
        )
        """

    conn.execute(text(create_sql))

    table = _quote_identifier(DB_TABLES[dataset])
    for column in _DATASET_COLUMNS[dataset]:
        sql_type = _COLUMN_SQL_TYPES.get(column, "TEXT")
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {_quote_identifier(column)} {sql_type}"))


def save_dashboard_record(dataset: str, row: dict, *, replace_keys: list[str] | None = None) -> str:
    if dataset not in DB_TABLES:
        raise ValueError(f"Dataset invalido: {dataset}")

    from sqlalchemy import text

    prepared = _prepare_insert_row(dataset, row)
    columns = [column for column, value in prepared.items() if value is not None]
    if not columns:
        raise ValueError("Nenhum dado valido para salvar.")

    table = _quote_identifier(DB_TABLES[dataset])
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    value_refs, value_params = _bind_columns(columns, prepared, "value")
    value_sql = ", ".join(value_refs[column] for column in columns)
    version = datetime.now(timezone.utc).isoformat()

    plate_registry_changed = dataset == "placas"
    text_registry_changed: set[str] = set()
    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        if replace_keys:
            keys = [key for key in replace_keys if key in prepared and prepared.get(key) is not None]
            if keys:
                replace_refs, replace_params = _bind_columns(keys, prepared, "replace")
                where_sql = " AND ".join(f"{_quote_identifier(key)} = {replace_refs[key]}" for key in keys)
                conn.execute(
                    text(f"DELETE FROM {table} WHERE {where_sql}"),
                    replace_params,
                )
        conn.execute(text(f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql})"), value_params)
        if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"} and prepared.get("PLACA") and prepared.get("Categoria"):
            plate_registry_changed = True
            _ensure_dataset_table(conn, "placas")
            placas_table = _quote_identifier(DB_TABLES["placas"])
            conn.execute(
                text(
                    f"""
                    INSERT INTO {placas_table} ("PLACA", "Categoria")
                    VALUES (:placa, :categoria)
                    ON CONFLICT ("PLACA")
                    DO UPDATE SET "Categoria" = EXCLUDED."Categoria"
                    """
                ),
                {"placa": prepared["PLACA"], "categoria": prepared["Categoria"]},
            )
            _write_metadata(conn, "placas.version", version)
        if dataset == "combustivel":
            registry_targets = (
                ("combustiveis", "Combustivel", prepared.get("Combustivel")),
                ("postos", "POSTOS", prepared.get("POSTOS")),
            )
            for registry_dataset, column, value in registry_targets:
                if not value:
                    continue
                text_registry_changed.add(registry_dataset)
                _ensure_dataset_table(conn, registry_dataset)
                registry_table = _quote_identifier(DB_TABLES[registry_dataset])
                quoted_column = _quote_identifier(column)
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {registry_table} ({quoted_column})
                        VALUES (:value)
                        ON CONFLICT ({quoted_column}) DO NOTHING
                        """
                    ),
                    {"value": value},
                )
                _write_metadata(conn, f"{registry_dataset}.version", version)
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    if plate_registry_changed:
        _clear_dataset_cache("placas")
    _clear_dataset_cache(dataset)
    for registry_dataset in text_registry_changed:
        _clear_dataset_cache(registry_dataset)
    return version


def replace_dashboard_records(dataset: str, rows: list[dict]) -> str:
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")

    from sqlalchemy import text

    columns = _DATASET_COLUMNS[dataset]
    prepared_rows = []
    for row in rows:
        prepared = _prepare_insert_row(dataset, row)
        if any(prepared.get(column) is not None for column in columns):
            prepared_rows.append(prepared)

    table = _quote_identifier(DB_TABLES[dataset])
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    version = datetime.now(timezone.utc).isoformat()
    text_registry_changed: set[str] = set()

    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        conn.execute(text(f"DELETE FROM {table}"))
        for prepared in prepared_rows:
            value_refs, value_params = _bind_columns(columns, prepared, "value")
            value_sql = ", ".join(value_refs[column] for column in columns)
            conn.execute(text(f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql})"), value_params)

            if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"} and prepared.get("PLACA") and prepared.get("Categoria"):
                _ensure_dataset_table(conn, "placas")
                placas_table = _quote_identifier(DB_TABLES["placas"])
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {placas_table} ("PLACA", "Categoria")
                        VALUES (:placa, :categoria)
                        ON CONFLICT ("PLACA")
                        DO UPDATE SET "Categoria" = EXCLUDED."Categoria"
                        """
                    ),
                    {"placa": prepared["PLACA"], "categoria": prepared["Categoria"]},
                )

            if dataset == "combustivel":
                registry_targets = (
                    ("combustiveis", "Combustivel", prepared.get("Combustivel")),
                    ("postos", "POSTOS", prepared.get("POSTOS")),
                )
                for registry_dataset, column, value in registry_targets:
                    if not value:
                        continue
                    text_registry_changed.add(registry_dataset)
                    _ensure_dataset_table(conn, registry_dataset)
                    registry_table = _quote_identifier(DB_TABLES[registry_dataset])
                    quoted_column = _quote_identifier(column)
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO {registry_table} ({quoted_column})
                            VALUES (:value)
                            ON CONFLICT ({quoted_column}) DO NOTHING
                            """
                        ),
                        {"value": value},
                    )
                    _write_metadata(conn, f"{registry_dataset}.version", version)

        if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
            _write_metadata(conn, "placas.version", version)
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
        _clear_dataset_cache("placas")
    _clear_dataset_cache(dataset)
    for registry_dataset in text_registry_changed:
        _clear_dataset_cache(registry_dataset)
    return version


def append_dashboard_records(dataset: str, rows: list[dict], *, update_plate_registry: bool = True) -> str:
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")

    from sqlalchemy import text

    columns = _DATASET_COLUMNS[dataset]
    prepared_rows = []
    for row in rows:
        prepared = _prepare_insert_row(dataset, row)
        if any(prepared.get(column) is not None for column in columns):
            prepared_rows.append(prepared)
    if not prepared_rows:
        raise ValueError("Nenhum dado valido para salvar.")

    table = _quote_identifier(DB_TABLES[dataset])
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    version = datetime.now(timezone.utc).isoformat()
    text_registry_changed: set[str] = set()

    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        for prepared in prepared_rows:
            value_refs, value_params = _bind_columns(columns, prepared, "value")
            value_sql = ", ".join(value_refs[column] for column in columns)
            conn.execute(text(f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql})"), value_params)

            if update_plate_registry and dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"} and prepared.get("PLACA") and prepared.get("Categoria"):
                _ensure_dataset_table(conn, "placas")
                placas_table = _quote_identifier(DB_TABLES["placas"])
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {placas_table} ("PLACA", "Categoria")
                        VALUES (:placa, :categoria)
                        ON CONFLICT ("PLACA")
                        DO UPDATE SET "Categoria" = EXCLUDED."Categoria"
                        """
                    ),
                    {"placa": prepared["PLACA"], "categoria": prepared["Categoria"]},
                )

            if dataset == "combustivel":
                registry_targets = (
                    ("combustiveis", "Combustivel", prepared.get("Combustivel")),
                    ("postos", "POSTOS", prepared.get("POSTOS")),
                )
                for registry_dataset, column, value in registry_targets:
                    if not value:
                        continue
                    text_registry_changed.add(registry_dataset)
                    _ensure_dataset_table(conn, registry_dataset)
                    registry_table = _quote_identifier(DB_TABLES[registry_dataset])
                    quoted_column = _quote_identifier(column)
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO {registry_table} ({quoted_column})
                            VALUES (:value)
                            ON CONFLICT ({quoted_column}) DO NOTHING
                            """
                        ),
                        {"value": value},
                    )
                    _write_metadata(conn, f"{registry_dataset}.version", version)

        if update_plate_registry and dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
            _write_metadata(conn, "placas.version", version)
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    if update_plate_registry and dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
        _clear_dataset_cache("placas")
    _clear_dataset_cache(dataset)
    for registry_dataset in text_registry_changed:
        _clear_dataset_cache(registry_dataset)
    return version


def _import_record_key(columns: list[str], row: dict) -> tuple:
    """Cria uma chave estavel para comparar registros da planilha com o Neon."""
    key = []
    for column in columns:
        value = _normalize_insert_value(row.get(column))
        if value is None:
            key.append(None)
            continue
        if column == "Data":
            parsed = pd.to_datetime(value, errors="coerce")
            key.append(parsed.date().isoformat() if pd.notna(parsed) else str(value).strip())
            continue
        if _COLUMN_SQL_TYPES.get(column) == "DOUBLE PRECISION":
            numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            key.append(round(float(numeric), 6) if pd.notna(numeric) else None)
            continue
        key.append(str(value).strip() if isinstance(value, str) else value)
    return tuple(key)


def append_missing_dashboard_records(
    dataset: str,
    rows: list[dict],
    *,
    update_plate_registry: bool = True,
) -> tuple[list[dict], int]:
    """Insere somente o que falta, em uma unica transacao atomica."""
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")

    from sqlalchemy import text

    columns = _DATASET_COLUMNS[dataset]
    prepared_rows = []
    for row in rows:
        prepared = _prepare_insert_row(dataset, row)
        if any(prepared.get(column) is not None for column in columns):
            prepared_rows.append(prepared)
    if not prepared_rows:
        raise ValueError("Nenhum dado valido para salvar.")

    table = _quote_identifier(DB_TABLES[dataset])
    quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
    version = datetime.now(timezone.utc).isoformat()
    inserted_rows: list[dict] = []
    skipped = 0
    text_registry_changed: set[str] = set()

    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        conn.execute(text(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE"))
        months = sorted(
            {
                str(row.get("Mes") or "").strip()
                for row in prepared_rows
                if str(row.get("Mes") or "").strip()
            }
        )
        select_sql = f"SELECT {quoted_columns} FROM {table}"
        select_params = {}
        if "Mes" in columns and months:
            month_refs = []
            for index, month in enumerate(months):
                name = f"month_{index}"
                month_refs.append(f":{name}")
                select_params[name] = month
            select_sql += f' WHERE "Mes" IN ({", ".join(month_refs)})'
        existing = conn.execute(text(select_sql), select_params).mappings().all()
        existing_counts = Counter(
            _import_record_key(columns, dict(row))
            for row in existing
        )

        for prepared in prepared_rows:
            key = _import_record_key(columns, prepared)
            if existing_counts[key] > 0:
                existing_counts[key] -= 1
                skipped += 1
            else:
                inserted_rows.append(prepared)

        if inserted_rows:
            parameter_names = {column: f"value_{index}" for index, column in enumerate(columns)}
            value_sql = ", ".join(f":{parameter_names[column]}" for column in columns)
            insert_params = [
                {parameter_names[column]: row.get(column) for column in columns}
                for row in inserted_rows
            ]
            conn.execute(
                text(f"INSERT INTO {table} ({quoted_columns}) VALUES ({value_sql})"),
                insert_params,
            )

            if update_plate_registry and dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
                plate_categories = {
                    str(row["PLACA"]): str(row["Categoria"])
                    for row in inserted_rows
                    if row.get("PLACA") and row.get("Categoria")
                }
                if plate_categories:
                    _ensure_dataset_table(conn, "placas")
                    placas_table = _quote_identifier(DB_TABLES["placas"])
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO {placas_table} ("PLACA", "Categoria")
                            VALUES (:placa, :categoria)
                            ON CONFLICT ("PLACA")
                            DO UPDATE SET "Categoria" = EXCLUDED."Categoria"
                            """
                        ),
                        [
                            {"placa": plate, "categoria": category}
                            for plate, category in plate_categories.items()
                        ],
                    )
                    _write_metadata(conn, "placas.version", version)

            if dataset == "combustivel":
                registry_values = {
                    "combustiveis": {
                        str(row["Combustivel"]).strip()
                        for row in inserted_rows
                        if row.get("Combustivel")
                    },
                    "postos": {
                        str(row["POSTOS"]).strip()
                        for row in inserted_rows
                        if row.get("POSTOS")
                    },
                }
                for registry_dataset, values in registry_values.items():
                    if not values:
                        continue
                    column = "Combustivel" if registry_dataset == "combustiveis" else "POSTOS"
                    text_registry_changed.add(registry_dataset)
                    _ensure_dataset_table(conn, registry_dataset)
                    registry_table = _quote_identifier(DB_TABLES[registry_dataset])
                    quoted_column = _quote_identifier(column)
                    conn.execute(
                        text(
                            f"""
                            INSERT INTO {registry_table} ({quoted_column})
                            VALUES (:value)
                            ON CONFLICT ({quoted_column}) DO NOTHING
                            """
                        ),
                        [{"value": value} for value in sorted(values)],
                    )
                    _write_metadata(conn, f"{registry_dataset}.version", version)

            _write_metadata(conn, f"{dataset}.version", version)
            _write_metadata(conn, "import.version", version)

    if inserted_rows:
        if update_plate_registry and dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
            _clear_dataset_cache("placas")
        _clear_dataset_cache(dataset)
        for registry_dataset in text_registry_changed:
            _clear_dataset_cache(registry_dataset)
    return inserted_rows, skipped


def upsert_dashboard_records(dataset: str, rows: list[dict], *, replace_keys: list[str]) -> str:
    """Insere várias linhas substituindo a combinação informada, em uma transação."""
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")
    keys = [key for key in replace_keys if key in _DATASET_COLUMNS[dataset]]
    if not keys:
        raise ValueError("Informe ao menos uma chave valida para substituir os registros.")

    from sqlalchemy import text

    columns = _DATASET_COLUMNS[dataset]
    prepared_rows = []
    for row in rows:
        prepared = _prepare_insert_row(dataset, row)
        if any(prepared.get(column) is not None for column in columns):
            if any(prepared.get(key) is None for key in keys):
                raise ValueError("Todas as chaves de substituicao devem estar preenchidas.")
            prepared_rows.append(prepared)
    if not prepared_rows:
        raise ValueError("Nenhum dado valido para salvar.")

    table = _quote_identifier(DB_TABLES[dataset])
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    version = datetime.now(timezone.utc).isoformat()
    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        for index, prepared in enumerate(prepared_rows):
            replace_refs, replace_params = _bind_columns(keys, prepared, f"replace_{index}")
            where_sql = " AND ".join(
                f"{_quote_identifier(key)} = {replace_refs[key]}" for key in keys
            )
            conn.execute(text(f"DELETE FROM {table} WHERE {where_sql}"), replace_params)
            value_refs, value_params = _bind_columns(columns, prepared, f"value_{index}")
            value_sql = ", ".join(value_refs[column] for column in columns)
            conn.execute(
                text(f"INSERT INTO {table} ({column_sql}) VALUES ({value_sql})"),
                value_params,
            )
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    _clear_dataset_cache(dataset)
    return version


def load_peso_route_template() -> pd.DataFrame:
    """Return one route row per peso record, preserving its database order."""
    from sqlalchemy import text

    table = _quote_identifier(DB_TABLES["peso"])
    try:
        engine = _db_engine()
        with engine.begin() as conn:
            _ensure_dataset_table(conn, "peso")
        df = pd.read_sql_query(
            text(
                f"""
                SELECT
                    "Data",
                    "Mes",
                    "PLACA",
                    COALESCE("Rota", '') AS "Rota"
                FROM {table}
                WHERE "Data" IS NOT NULL
                  AND COALESCE(TRIM("PLACA"), '') <> ''
                ORDER BY CAST("Data" AS DATE), "PLACA", ctid
                """
            ),
            engine,
        )
    except Exception as exc:
        raise RuntimeError("Nao foi possivel carregar as datas de peso no Neon.") from exc

    df["Data"] = pd.to_datetime(df.get("Data"), errors="coerce")
    return df[["Data", "Mes", "PLACA", "Rota"]].copy()


def _prepare_peso_route_rows(rows: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    for index, row in enumerate(rows, start=2):
        parsed_date = pd.to_datetime(row.get("Data"), dayfirst=True, errors="coerce")
        normalized_plate = _normalize_plate_value(row.get("PLACA"))
        plate = "" if pd.isna(normalized_plate) else str(normalized_plate).strip()
        route = str(row.get("Rota") or "").strip().upper()
        if pd.isna(parsed_date):
            raise ValueError(f"Linha {index}: data invalida.")
        if not plate:
            raise ValueError(f"Linha {index}: placa nao preenchida.")
        route_date = parsed_date.date()
        prepared.append({"Data": route_date, "PLACA": plate, "Rota": route})
    if not prepared:
        raise ValueError("Nenhuma rota valida para importar.")
    return prepared


def _peso_route_plate_match_key(value) -> str:
    normalized = _normalize_plate_value(value)
    if pd.isna(normalized):
        return ""
    return str(normalized).translate(str.maketrans({"I": "1", "L": "1", "O": "0", "Q": "0"}))


def _match_peso_route_rows(
    conn,
    rows: list[dict],
    *,
    lock: bool,
) -> tuple[list[dict], list[str], list[str]]:
    from sqlalchemy import text

    prepared = _prepare_peso_route_rows(rows)
    grouped: dict[tuple[object, str], list[dict]] = defaultdict(list)
    for row in prepared:
        grouped[(row["Data"], row["PLACA"])].append(row)

    table = _quote_identifier(DB_TABLES["peso"])
    matches: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    lock_sql = " FOR UPDATE" if lock else ""
    for (route_date, plate), route_rows in grouped.items():
        def load_candidates(candidate_plate: str):
            return conn.execute(
                text(
                    f"""
                    SELECT
                        ctid::text AS "__row_id",
                        "Data",
                        "Cidade",
                        "PLACA",
                        "Categoria",
                        "Peso",
                        "Valor",
                        "Rota" AS "RotaAtual"
                    FROM {table}
                    WHERE CAST("Data" AS DATE) = :route_date
                      AND "PLACA" = :plate
                    ORDER BY ctid
                    {lock_sql}
                    """
                ),
                {"route_date": route_date, "plate": candidate_plate},
            ).mappings().all()

        matched_plate = plate
        candidates = load_candidates(matched_plate)
        if not candidates:
            available_rows = conn.execute(
                text(
                    f"""
                    SELECT DISTINCT "PLACA"
                    FROM {table}
                    WHERE CAST("Data" AS DATE) = :route_date
                      AND COALESCE(TRIM("PLACA"), '') <> ''
                    ORDER BY "PLACA"
                    """
                ),
                {"route_date": route_date},
            ).scalars().all()
            available_plates = [str(value).strip() for value in available_rows if str(value or "").strip()]
            input_key = _peso_route_plate_match_key(plate)
            similar_plates = [
                value for value in available_plates if _peso_route_plate_match_key(value) == input_key
            ]
            if len(similar_plates) == 1:
                matched_plate = similar_plates[0]
                candidates = load_candidates(matched_plate)
            elif len(similar_plates) > 1:
                errors.append(
                    f"{route_date.strftime('%d/%m/%Y')} - {plate}: placa ambigua. "
                    f"Pode corresponder a {', '.join(similar_plates)}."
                )
                continue
            else:
                available_text = ", ".join(available_plates[:8])
                if len(available_plates) > 8:
                    available_text += ", ..."
                detail = (
                    f" Placas cadastradas nessa data: {available_text}."
                    if available_text
                    else " Nao ha placas cadastradas nessa data."
                )
                warnings.append(
                    f"{route_date.strftime('%d/%m/%Y')} - {plate}: nenhum registro de peso encontrado."
                    f"{detail} {len(route_rows)} linha(s) sera(ao) ignorada(s)."
                )
                continue

        if len(candidates) != len(route_rows):
            errors.append(
                f"{route_date.strftime('%d/%m/%Y')} - {plate}"
                f"{f' (cadastrada como {matched_plate})' if matched_plate != plate else ''}: "
                f"{len(route_rows)} linha(s) na planilha e {len(candidates)} registro(s) de peso."
            )
            continue

        for route_row, candidate in zip(route_rows, candidates):
            match = dict(candidate)
            match["Data"] = route_date
            match["PLACAInformada"] = plate
            match["Rota"] = route_row["Rota"]
            matches.append(match)
    return matches, errors, warnings


def preview_peso_route_updates(rows: list[dict]) -> dict:
    engine = _db_engine()
    with engine.begin() as conn:
        _ensure_dataset_table(conn, "peso")
        matches, errors, warnings = _match_peso_route_rows(conn, rows, lock=False)
    public_matches = [{key: value for key, value in row.items() if key != "__row_id"} for row in matches]
    return {"matches": public_matches, "errors": errors, "warnings": warnings}


def update_peso_routes(rows: list[dict]) -> int:
    from sqlalchemy import text

    table = _quote_identifier(DB_TABLES["peso"])
    version = datetime.now(timezone.utc).isoformat()
    engine = _db_engine()
    with engine.begin() as conn:
        _ensure_dataset_table(conn, "peso")
        matches, errors, _warnings = _match_peso_route_rows(conn, rows, lock=True)
        if errors:
            raise ValueError("A importacao foi cancelada. " + " ".join(errors))

        if matches:
            conn.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET "Rota" = :route
                    WHERE ctid = CAST(:row_id AS tid)
                    """
                ),
                [{"route": match["Rota"], "row_id": match["__row_id"]} for match in matches],
            )

        _write_metadata(conn, "peso.version", version)
        _write_metadata(conn, "import.version", version)

    _clear_dataset_cache("peso")
    return len(matches)


def delete_matching_dashboard_records(dataset: str, rows: list[dict]) -> int:
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")

    from sqlalchemy import text

    columns = _DATASET_COLUMNS[dataset]
    prepared_rows = []
    for row in rows:
        prepared = _prepare_insert_row(dataset, row)
        if any(prepared.get(column) is not None for column in columns):
            prepared_rows.append(prepared)
    if not prepared_rows:
        raise ValueError("Nenhum dado valido para apagar.")

    table = _quote_identifier(DB_TABLES[dataset])
    version = datetime.now(timezone.utc).isoformat()
    deleted = 0

    with _db_engine().begin() as conn:
        for prepared in prepared_rows:
            match_refs, match_params = _bind_columns(columns, prepared, "match")
            where_sql = " AND ".join(f"{_quote_identifier(column)} IS NOT DISTINCT FROM {match_refs[column]}" for column in columns)
            result = conn.execute(
                text(
                    f"""
                    DELETE FROM {table}
                    WHERE ctid IN (
                        SELECT ctid FROM {table}
                        WHERE {where_sql}
                        LIMIT 1
                    )
                    """
                ),
                match_params,
            )
            deleted += max(result.rowcount or 0, 0)

        if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
            _write_metadata(conn, "placas.version", version)
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
        _clear_dataset_cache("placas")
    _clear_dataset_cache(dataset)
    return deleted


def delete_dashboard_month(dataset: str, mes: str) -> int:
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")

    mes_value = str(mes or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", mes_value):
        raise ValueError("Mes invalido. Use o formato YYYY-MM.")
    year, month = (int(part) for part in mes_value.split("-"))
    start_date = datetime(year, month, 1)
    end_date = datetime(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)

    from sqlalchemy import text

    table = _quote_identifier(DB_TABLES[dataset])
    version = datetime.now(timezone.utc).isoformat()

    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        if "Data" in _DATASET_COLUMNS[dataset]:
            delete_sql = f"DELETE FROM {table} WHERE \"Mes\" = :mes OR (\"Data\" >= :start_date AND \"Data\" < :end_date)"
            params = {"mes": mes_value, "start_date": start_date, "end_date": end_date}
        else:
            delete_sql = f"DELETE FROM {table} WHERE \"Mes\" = :mes"
            params = {"mes": mes_value}
        result = conn.execute(text(delete_sql), params)
        deleted = max(result.rowcount or 0, 0)

        if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
            _write_metadata(conn, "placas.version", version)
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
        _clear_dataset_cache("placas")
    _clear_dataset_cache(dataset)
    return deleted


def delete_dashboard_all(dataset: str) -> int:
    if dataset not in DB_TABLES or dataset not in _DATASET_COLUMNS:
        raise ValueError(f"Dataset invalido: {dataset}")

    from sqlalchemy import text

    table = _quote_identifier(DB_TABLES[dataset])
    version = datetime.now(timezone.utc).isoformat()

    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, dataset)
        result = conn.execute(text(f"DELETE FROM {table}"))
        deleted = max(result.rowcount or 0, 0)

        if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
            _write_metadata(conn, "placas.version", version)
        _write_metadata(conn, f"{dataset}.version", version)
        _write_metadata(conn, "import.version", version)

    if dataset in {"combustivel", "manutencao", "pneus", "pedagio", "peso"}:
        _clear_dataset_cache("placas")
    _clear_dataset_cache(dataset)
    return deleted


def _month_sequence(start_mes: str, end_mes: str) -> list[str]:
    if not re.fullmatch(r"\d{4}-\d{2}", str(start_mes or "")) or not re.fullmatch(r"\d{4}-\d{2}", str(end_mes or "")):
        raise ValueError("Periodo invalido. Use o formato YYYY-MM.")
    start_year, start_month = (int(part) for part in start_mes.split("-"))
    end_year, end_month = (int(part) for part in end_mes.split("-"))
    cursor = datetime(start_year, start_month, 1)
    end = datetime(end_year, end_month, 1)
    if cursor > end:
        raise ValueError("Periodo inicial maior que o final.")

    months = []
    while cursor <= end:
        months.append(cursor.strftime("%Y-%m"))
        cursor = datetime(cursor.year + (1 if cursor.month == 12 else 0), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return months


def redistribute_pedagio_seguros_period(
    start_mes: str = "2025-10",
    end_mes: str = "2026-10",
    *,
    source_tipos: list[str] | None = None,
    source_meses: list[str] | None = None,
) -> dict:
    months = _month_sequence(start_mes, end_mes)
    if not months:
        raise ValueError("Periodo sem meses para aplicar.")

    from sqlalchemy import text

    normalized_source_tipos = {_normalize_tipo_value(tipo) for tipo in (source_tipos or ["Seguro"]) if str(tipo or "").strip()}
    if not normalized_source_tipos:
        raise ValueError("Selecione pelo menos um tipo atual para ajustar.")
    normalized_source_meses = {str(mes).strip() for mes in (source_meses or []) if str(mes or "").strip()}

    table = _quote_identifier(DB_TABLES["pedagio"])
    version = datetime.now(timezone.utc).isoformat()
    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, "pedagio")
        rows = conn.execute(
            text(
                f"""
                SELECT ctid::text AS row_id, "Tipo", "Data", "Mes", "PLACA", "Categoria", "Custo"
                FROM {table}
                """
            )
        ).mappings().all()
        seguros = [
            dict(row)
            for row in rows
            if _normalize_tipo_value(row.get("Tipo")) in normalized_source_tipos
            and (not normalized_source_meses or str(row.get("Mes") or "").strip() in normalized_source_meses)
        ]
        seguros.sort(
            key=lambda row: (
                str(row.get("Mes") or ""),
                str(row.get("Data") or ""),
                str(row.get("PLACA") or ""),
                float(row.get("Custo") or 0),
                str(row.get("row_id") or ""),
            )
        )

        deleted = 0
        inserted = 0
        deleted_rows = []
        month_counts = {mes: 0 for mes in months}
        for row in seguros:
            result = conn.execute(
                text(
                    f"""
                    DELETE FROM {table}
                    WHERE ctid::text = :row_id
                    """
                ),
                {"row_id": row["row_id"]},
            )
            if not max(result.rowcount or 0, 0):
                continue

            deleted += 1
            deleted_rows.append(row)

        grouped_by_plate: dict[tuple[str | None, str], list[dict]] = {}
        for row in deleted_rows:
            placa = _normalize_plate_value(row.get("PLACA"))
            if pd.isna(placa):
                placa = None
            categoria = _normalize_category_value(row.get("Categoria") or "Transporte")
            key = (placa, categoria)
            grouped_by_plate.setdefault(key, []).append(row)

        repaired_groups = 0
        for (placa, categoria), plate_rows in grouped_by_plate.items():
            monthly_totals: dict[str, float] = {}
            for row in plate_rows:
                mes = str(row.get("Mes") or "").strip()
                if mes in month_counts:
                    monthly_totals[mes] = monthly_totals.get(mes, 0.0) + float(row.get("Custo") or 0.0)

            if len(monthly_totals) > 1:
                custo_base = sum(monthly_totals.values()) / len(monthly_totals)
                repaired_groups += 1
            else:
                custo_base = sum(float(row.get("Custo") or 0.0) for row in plate_rows)

            custo_mensal = custo_base
            for mes in months:
                year, month = (int(part) for part in mes.split("-"))
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {table} ("PLACA", "Tipo", "Custo", "Mes", "Data", "Categoria")
                        VALUES (:placa, :tipo, :custo, :mes, :data, :categoria)
                        """
                    ),
                    {
                        "placa": placa,
                        "tipo": "Seguro",
                        "custo": custo_mensal,
                        "mes": mes,
                        "data": datetime(year, month, 1),
                        "categoria": categoria,
                    },
                )
                inserted += 1
                month_counts[mes] += 1

        _write_metadata(conn, "pedagio.version", version)
        _write_metadata(conn, "import.version", version)

    _clear_dataset_cache("pedagio")
    return {"updated": inserted, "deleted": deleted, "plates": len(grouped_by_plate), "repaired": repaired_groups, "total": len(seguros), "months": months, "month_counts": month_counts}


def replace_pedagio_seguros_por_placa(
    records: list[dict],
    start_mes: str = "2025-10",
    end_mes: str = "2026-10",
) -> dict:
    months = _month_sequence(start_mes, end_mes)
    if not months:
        raise ValueError("Periodo sem meses para aplicar.")

    normalized: dict[tuple[str, str], float] = {}
    for record in records or []:
        placa = _normalize_plate_value(record.get("PLACA"))
        if pd.isna(placa) or not _is_plate_or_asset_identifier(placa):
            continue
        categoria = _normalize_category_value(record.get("Categoria") or "Transporte")
        custo = float(record.get("Custo") or 0.0)
        if custo <= 0:
            continue
        key = (str(placa), categoria)
        normalized[key] = normalized.get(key, 0.0) + custo

    from sqlalchemy import text

    table = _quote_identifier(DB_TABLES["pedagio"])
    version = datetime.now(timezone.utc).isoformat()
    deleted = 0
    inserted = 0
    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, "pedagio")
        rows = conn.execute(
            text(
                f"""
                SELECT ctid::text AS row_id, "Tipo", "Mes"
                FROM {table}
                """
            )
        ).mappings().all()
        for row in rows:
            if _normalize_tipo_value(row.get("Tipo")) != "Seguro":
                continue
            if str(row.get("Mes") or "").strip() not in months:
                continue
            result = conn.execute(
                text(
                    f"""
                    DELETE FROM {table}
                    WHERE ctid::text = :row_id
                    """
                ),
                {"row_id": row["row_id"]},
            )
            deleted += max(result.rowcount or 0, 0)

        for (placa, categoria), custo in normalized.items():
            for mes in months:
                year, month = (int(part) for part in mes.split("-"))
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {table} ("PLACA", "Tipo", "Custo", "Mes", "Data", "Categoria")
                        VALUES (:placa, :tipo, :custo, :mes, :data, :categoria)
                        """
                    ),
                    {
                        "placa": placa,
                        "tipo": "Seguro",
                        "custo": custo,
                        "mes": mes,
                        "data": datetime(year, month, 1),
                        "categoria": categoria,
                    },
                )
                inserted += 1
        _write_metadata(conn, "pedagio.version", version)
        _write_metadata(conn, "import.version", version)

    _clear_dataset_cache("pedagio")
    return {"deleted": deleted, "inserted": inserted, "plates": len(normalized), "months": months}


def rename_plate(old_plate, new_plate, categoria: str, diaria: float | None = None) -> str:
    old_value = _normalize_plate_value(old_plate)
    new_value = _normalize_plate_value(new_plate)
    if not _is_plate_or_asset_identifier(old_value):
        raise ValueError("Placa/equipamento original invalido.")
    if not _is_plate_or_asset_identifier(new_value):
        raise ValueError("Nova placa/equipamento invalido.")

    categoria_value = _normalize_category_value(categoria)
    version = datetime.now(timezone.utc).isoformat()

    from sqlalchemy import text

    with _db_engine().begin() as conn:
        _ensure_dataset_table(conn, "placas")
        placas_table = _quote_identifier(DB_TABLES["placas"])
        if diaria is None:
            stored_daily = conn.execute(
                text(
                    f"""
                    SELECT "Diaria"
                    FROM {placas_table}
                    WHERE "PLACA" IN (:old_plate, :new_plate)
                    ORDER BY CASE WHEN "PLACA" = :old_plate THEN 0 ELSE 1 END
                    LIMIT 1
                    """
                ),
                {"old_plate": old_value, "new_plate": new_value},
            ).scalar()
            diaria_value = float(stored_daily or 0.0)
        else:
            diaria_value = max(float(diaria or 0.0), 0.0)
        for dataset in ("combustivel", "combustivel_km", "empilhadeira_horas", "manutencao", "pneus", "pedagio", "peso", "rodagem_rota"):
            _ensure_dataset_table(conn, dataset)
            table = _quote_identifier(DB_TABLES[dataset])
            conn.execute(
                text(f"UPDATE {table} SET \"PLACA\" = :new_plate WHERE \"PLACA\" = :old_plate"),
                {"new_plate": new_value, "old_plate": old_value},
            )
            if "Categoria" in _DATASET_COLUMNS[dataset]:
                conn.execute(
                    text(f"UPDATE {table} SET \"Categoria\" = :categoria WHERE \"PLACA\" = :new_plate"),
                    {"new_plate": new_value, "categoria": categoria_value},
                )
            _write_metadata(conn, f"{dataset}.version", version)

        conn.execute(text(f"DELETE FROM {placas_table} WHERE \"PLACA\" IN (:old_plate, :new_plate)"), {"old_plate": old_value, "new_plate": new_value})
        conn.execute(
            text(f"INSERT INTO {placas_table} (\"PLACA\", \"Categoria\", \"Diaria\") VALUES (:placa, :categoria, :diaria)"),
            {"placa": new_value, "categoria": categoria_value, "diaria": diaria_value},
        )
        _write_metadata(conn, "placas.version", version)
        _write_metadata(conn, "import.version", version)

    _clear_dataset_cache("placas")
    return version


def _normalize_ascii(value):
    if pd.isna(value):
        return value
    return unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").strip()


def _normalize_plate_value(value):
    if pd.isna(value):
        return pd.NA
    text = _normalize_ascii(value).upper()
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text in {"NAN", "NONE", "NAT", "<NA>"}:
        return pd.NA
    if "SEM" in text and "PLACA" in text:
        return "SEM PLACA"
    compact = re.sub(r"[^A-Z0-9]", "", text)
    match = re.search(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}|[A-Z]{3}[0-9]{4}", compact)
    if match:
        plate = match.group(0)
        return _PLATE_ALIASES.get(plate, plate)
    return _PLATE_ALIASES.get(text, text)


def _is_plate_identifier(value) -> bool:
    if pd.isna(value):
        return False
    text = str(value).strip().upper()
    if text == "SEM PLACA":
        return True
    return bool(re.fullmatch(r"[A-Z]{3}[0-9][A-Z0-9][0-9]{2}|[A-Z]{3}[0-9]{4}", text))


def _is_plate_or_asset_identifier(value) -> bool:
    if _is_plate_identifier(value):
        return True
    if pd.isna(value):
        return False
    text = _normalize_ascii(value).upper().strip()
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9 ]{1,39}", text))


def _is_forklift_identifier(value) -> bool:
    if pd.isna(value):
        return False
    text = _normalize_ascii(value).upper().strip()
    return "EMPILHADEIRA" in text


def _is_equipment_identifier(value) -> bool:
    if pd.isna(value):
        return False
    text = _normalize_ascii(value).upper().strip()
    return "EQUIPAMENTO" in text


def _normalize_plate_series(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="string")
    return series.apply(_normalize_plate_value).astype("string")


def _normalize_text_column(df: pd.DataFrame, column: str) -> None:
    if column not in df.columns:
        return
    series = df[column].astype("string").str.strip()
    series = series.mask(series.str.lower().isin(["", "nan", "none", "nat", "<na>"]), pd.NA)
    df[column] = series


def _normalize_combustivel_value(value):
    if pd.isna(value):
        return pd.NA
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text or text.lower() in {"nan", "none", "nat", "<na>"}:
        return pd.NA

    key = _normalize_ascii(text).upper()
    compact = re.sub(r"[^A-Z0-9]", "", key)
    if compact in {"DIESELS10", "S10", "DIESELS010"}:
        return "Diesel S10"
    if compact in {"DIESELS50", "S50", "DIESELS050"}:
        return "Diesel S50"
    if compact == "DIESEL":
        return "Diesel"
    if "ARLA" in compact and "32" in compact:
        return "Arla 32"
    if "GASOLINAADITIVADA" in compact:
        return "Gasolina Aditivada"
    if "GASOLINACOMUM" in compact:
        return "Gasolina Comum"

    normalized = text.title()
    normalized = re.sub(r"\bS\s*(\d+)\b", lambda match: f"S{match.group(1)}", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bArla\b", "Arla", normalized, flags=re.IGNORECASE)
    return normalized


def _normalize_combustivel_column(df: pd.DataFrame) -> None:
    if "Combustivel" not in df.columns:
        return
    df["Combustivel"] = df["Combustivel"].apply(_normalize_combustivel_value).astype("string")


def _normalize_category_value(value, *, default: str = "Transporte") -> str:
    try:
        missing = pd.isna(value)
    except TypeError:
        missing = False
    raw = str(default if missing or value is None else value).strip()
    key = _normalize_ascii(raw).lower()
    if key == "vex":
        return "Vex"
    if key in {"freteiro", "freteiros", "frete"}:
        return "Freteiro"
    if key in {"empilhadeira", "empilhadeiras"}:
        return "Empilhadeira"
    if key in {"equipamento", "equipamentos"}:
        return "Equipamento"
    return "Transporte"


def _normalize_category_column(df: pd.DataFrame, *, default: str = "Transporte") -> None:
    if "Categoria" not in df.columns:
        df["Categoria"] = default
    else:
        _normalize_text_column(df, "Categoria")
        df["Categoria"] = df["Categoria"].fillna(default)
        df["Categoria"] = df["Categoria"].apply(lambda value: _normalize_category_value(value, default=default))
    _force_equipment_category(df)


def _force_equipment_category(df: pd.DataFrame) -> None:
    if df.empty or "PLACA" not in df.columns or "Categoria" not in df.columns:
        return
    forklift_mask = df["PLACA"].apply(_is_forklift_identifier)
    if forklift_mask.any():
        df.loc[forklift_mask, "Categoria"] = "Empilhadeira"
    mask = df["PLACA"].apply(_is_equipment_identifier)
    if mask.any():
        df.loc[mask, "Categoria"] = "Equipamento"


def _normalize_tipo_value(value):
    if pd.isna(value):
        return "Outros"
    text = _normalize_ascii(value).upper()
    if not text:
        return "Outros"
    if "PEDAG" in text:
        return "Pedagio"
    if "IPVA" in text:
        return "IPVA"
    if "SEGUR" in text or "APOLI" in text:
        return "Seguro"
    if "TAXI" in text:
        return "Taxi"
    if "EXTRA" in text:
        return "Extras"
    if "LICENCI" in text:
        return "Licenciamento"
    if "DPVAT" in text:
        return "DPVAT"
    return str(value).strip().title()


def _read_plate_registry() -> pd.DataFrame:
    cache = _PLATE_REGISTRY_CACHE
    with cache["lock"]:
        version = _db_version("placas")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()

        try:
            df = _read_database_table("placas", _PLACAS_COLUMNS)
        except Exception:
            df = _empty(_PLACAS_COLUMNS)
        if not df.empty:
            df = df[_PLACAS_COLUMNS].copy()
            df["PLACA"] = _normalize_plate_series(df["PLACA"])
            _normalize_category_column(df)
            df["Diaria"] = pd.to_numeric(df["Diaria"], errors="coerce").fillna(0.0).clip(lower=0)
            df = df.dropna(subset=["PLACA"]).drop_duplicates(subset=["PLACA"], keep="last")
            df = df[df["PLACA"].apply(_is_plate_or_asset_identifier)]
        else:
            df = _empty(_PLACAS_COLUMNS)

        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy()


def _apply_plate_categories(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "PLACA" not in df.columns:
        return df
    registry = _read_plate_registry()
    if registry.empty:
        return df
    mapping = dict(zip(registry["PLACA"].astype("string"), registry["Categoria"].astype("string")))
    if not mapping:
        return df
    df = df.copy()
    if "Categoria" not in df.columns:
        df["Categoria"] = "Transporte"
    mapped = df["PLACA"].astype("string").map(mapping)
    mask = mapped.notna()
    df.loc[mask, "Categoria"] = mapped.loc[mask]
    return df


def _derived_plate_registry() -> pd.DataFrame:
    frames = []
    for loader in (
        load_combustivel,
        load_combustivel_km,
        load_empilhadeira_horas,
        load_manutencao,
        load_pneus,
        load_pedagio,
        load_peso,
        load_rodagem_rota,
    ):
        try:
            df = loader()
        except Exception:
            continue
        if df.empty or "PLACA" not in df.columns:
            continue
        cols = ["PLACA", "Categoria"] if "Categoria" in df.columns else ["PLACA"]
        frame = df[cols].copy()
        if "Categoria" not in frame.columns:
            frame["Categoria"] = "Transporte"
        frames.append(frame)
    if not frames:
        return _empty(_PLACAS_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["PLACA"] = _normalize_plate_series(df["PLACA"])
    _normalize_category_column(df)
    df = df.dropna(subset=["PLACA"])
    df = df[df["PLACA"].apply(_is_plate_or_asset_identifier)]
    if df.empty:
        return _empty(_PLACAS_COLUMNS)
    grouped = (
        df.groupby("PLACA", as_index=False)["Categoria"]
        .agg(
            lambda values: (
                "Empilhadeira"
                if any(_normalize_category_value(value) == "Empilhadeira" for value in values)
                else (
                    "Equipamento"
                    if any(_normalize_category_value(value) == "Equipamento" for value in values)
                    else (
                        "Vex"
                        if any(_normalize_category_value(value) == "Vex" for value in values)
                        else ("Freteiro" if any(_normalize_category_value(value) == "Freteiro" for value in values) else "Transporte")
                    )
                )
            )
        )
    )
    grouped["Diaria"] = 0.0
    return grouped.sort_values("PLACA").reset_index(drop=True)


def load_placas() -> pd.DataFrame:
    version = (
        _db_version("placas"),
        _db_version("combustivel"),
        _db_version("combustivel_km"),
        _db_version("empilhadeira_horas"),
        _db_version("manutencao"),
        _db_version("pneus"),
        _db_version("pedagio"),
        _db_version("peso"),
        _db_version("rodagem_rota"),
    )
    with _PLACAS_CACHE["lock"]:
        cached = _PLACAS_CACHE.get("df")
        if cached is not None and _PLACAS_CACHE.get("mtime") == version:
            return cached.copy()

    derived = _derived_plate_registry()
    registered = _read_plate_registry()
    frames = [df for df in (derived, registered) if not df.empty]
    if not frames:
        df = _empty(_PLACAS_COLUMNS)
    else:
        df = pd.concat(frames, ignore_index=True)
        df["PLACA"] = _normalize_plate_series(df["PLACA"])
        _normalize_category_column(df)
        df["Diaria"] = pd.to_numeric(df["Diaria"], errors="coerce").fillna(0.0).clip(lower=0)
        df = df.dropna(subset=["PLACA"]).drop_duplicates(subset=["PLACA"], keep="last")
        df = df[df["PLACA"].apply(_is_plate_or_asset_identifier)]
        df = df[_PLACAS_COLUMNS].sort_values("PLACA").reset_index(drop=True)

    with _PLACAS_CACHE["lock"]:
        _PLACAS_CACHE["mtime"] = version
        _PLACAS_CACHE["df"] = df.copy()
    return df.copy()


def _normalize_mes(df: pd.DataFrame) -> None:
    if "Mes" not in df.columns:
        df["Mes"] = pd.NA

    mes_raw = df["Mes"]
    mes_text = mes_raw.astype("string").str.strip()
    mes_dt = pd.to_datetime(mes_raw, errors="coerce")
    valid_mes = mes_dt.notna()
    if valid_mes.any():
        mes_text.loc[valid_mes] = mes_dt.loc[valid_mes].dt.to_period("M").astype(str)

    if "Data" in df.columns:
        data_dt = pd.to_datetime(df["Data"], errors="coerce")
        empty_mes = mes_text.isna() | mes_text.str.lower().isin(["", "nan", "none", "nat", "<na>"])
        valid_data = empty_mes & data_dt.notna()
        if valid_data.any():
            mes_text.loc[valid_data] = data_dt.loc[valid_data].dt.to_period("M").astype(str)

    mes_text = mes_text.mask(mes_text.str.lower().isin(["", "nan", "none", "nat", "<na>"]), pd.NA)
    df["Mes"] = mes_text


def _group_sum(
    df: pd.DataFrame,
    group_col: str,
    value_col: str = "Custo",
    *,
    sort_by: str = "value",
) -> dict:
    if df is None or df.empty or group_col not in df.columns or value_col not in df.columns:
        return {group_col: [], value_col: []}

    data = df.dropna(subset=[group_col]).copy()
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    data = data.dropna(subset=[value_col])
    if data.empty:
        return {group_col: [], value_col: []}

    grouped = data.groupby(group_col, as_index=False)[value_col].sum()
    if sort_by == "group":
        grouped = grouped.sort_values(group_col)
    else:
        grouped = grouped.sort_values(value_col, ascending=False)
    return grouped.to_dict(orient="list")


def _unique_sorted(df: pd.DataFrame, column: str) -> list:
    if df is None or column not in df.columns:
        return []
    series = df[column].dropna()
    if series.empty:
        return []
    series = series.astype("string").str.strip()
    series = series[(series != "") & (~series.str.lower().isin(["nan", "none", "nat", "<na>"]))]
    return sorted(series.unique().tolist())


def _unique_years(df: pd.DataFrame) -> list[int]:
    if df is None or "Mes" not in df.columns:
        return []
    periodos = pd.to_datetime(df["Mes"], errors="coerce")
    return sorted({int(ano) for ano in periodos.dt.year.dropna().unique()})


def _parse_int(value, *, min_value: int | None = None, max_value: int | None = None) -> int | None:
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item not in (None, "", "Todos")), None)
    if value in (None, "", "Todos"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if min_value is not None and parsed < min_value:
        return None
    if max_value is not None and parsed > max_value:
        return None
    return parsed


def _normalize_categoria(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="string")
    return series.astype("string").fillna("").apply(lambda value: _normalize_ascii(value).strip().lower())


def _exclude_vex(df: pd.DataFrame) -> pd.DataFrame:
    if "Categoria" not in df.columns:
        return df
    mask = _normalize_categoria(df["Categoria"]) != "vex"
    return df.loc[mask].copy()


def _only_transporte(df: pd.DataFrame) -> pd.DataFrame:
    if "Categoria" not in df.columns:
        return df.iloc[0:0].copy()
    mask = _normalize_categoria(df["Categoria"]) == "transporte"
    return df.loc[mask].copy()


def _only_vex(df: pd.DataFrame) -> pd.DataFrame:
    if "Categoria" not in df.columns:
        return df.iloc[0:0].copy()
    mask = _normalize_categoria(df["Categoria"]) == "vex"
    return df.loc[mask].copy()


def _registered_plates_for_category(categoria: str) -> set[str]:
    try:
        registry = load_placas()
    except Exception:
        return set()
    if registry.empty or "PLACA" not in registry.columns or "Categoria" not in registry.columns:
        return set()
    target = _normalize_category_value(categoria).lower()
    mask = _normalize_categoria(registry["Categoria"]) == target
    return set(registry.loc[mask, "PLACA"].astype("string").dropna().tolist())


def _only_registered_category(df: pd.DataFrame, categoria: str) -> pd.DataFrame:
    if df.empty or "PLACA" not in df.columns:
        return df.iloc[0:0].copy()
    plates = _registered_plates_for_category(categoria)
    if not plates:
        target = _normalize_category_value(categoria)
        if target == "Vex":
            return _only_vex(df)
        if target == "Transporte":
            return _only_transporte(df)
        return df.iloc[0:0].copy()
    filtered = df[df["PLACA"].isin(plates)].copy()
    filtered["Categoria"] = _normalize_category_value(categoria)
    return filtered


def _filter_category_param(df: pd.DataFrame, categoria: str | None) -> pd.DataFrame:
    if not categoria or categoria == "Todos":
        return df.copy()
    if df.empty or "Categoria" not in df.columns:
        return df.iloc[0:0].copy()
    target = _normalize_category_value(categoria).lower()
    mask = _normalize_categoria(df["Categoria"]) == target
    return df.loc[mask].copy()


def _filter_by_period(
    df: pd.DataFrame,
    *,
    ano: int | None = None,
    mes: int | None = None,
    meses: list[int] | None = None,
) -> pd.DataFrame:
    meses = meses or []
    if df.empty or "Mes" not in df.columns or (ano is None and mes is None and not meses):
        return df
    periodos = pd.to_datetime(df["Mes"], errors="coerce")
    mask = periodos.notna()
    if ano is not None:
        mask &= periodos.dt.year == ano
    if mes is not None:
        mask &= periodos.dt.month == mes
    if meses:
        mask &= periodos.dt.month.isin(meses)
    return df.loc[mask].copy()


def _as_list(raw) -> list:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    return [raw]


def _parse_mes_list(raw) -> list[str]:
    meses: list[str] = []
    for value in _as_list(raw):
        if value in (None, "", "Todos"):
            continue
        for part in str(value).split(","):
            mes = part.strip()
            if mes and mes.lower() != "todos":
                meses.append(mes)
    return meses


def _parse_mes_int_list(raw) -> list[int]:
    meses: list[int] = []
    for value in _as_list(raw):
        if value in (None, "", "Todos"):
            continue
        for part in str(value).split(","):
            part = part.strip()
            if not part or part.lower() == "todos":
                continue
            try:
                num = int(part)
            except (TypeError, ValueError):
                parsed = pd.to_datetime(part, errors="coerce")
                if pd.isna(parsed):
                    continue
                num = int(parsed.month)
            if 1 <= num <= 12:
                meses.append(num)
    return meses


def _param(params: dict | None, key: str):
    if not params:
        return None
    value = params.get(key)
    if isinstance(value, (list, tuple)):
        return next((item for item in value if item not in (None, "")), None)
    return value


def _filter_plate_param(df: pd.DataFrame, placa: object) -> pd.DataFrame:
    if not placa or placa == "Todos" or df.empty or "PLACA" not in df.columns:
        return df
    target = _normalize_plate_value(placa)
    if pd.isna(target):
        return df.iloc[0:0].copy()
    normalized = _normalize_plate_series(df["PLACA"])
    return df.loc[normalized == str(target)].copy()


def _weekly_series(df: pd.DataFrame, date_col: str, value_col: str, label: str) -> dict:
    today = pd.Timestamp.today().normalize()
    start_default = today - pd.Timedelta(days=6)
    default_index = pd.date_range(start_default, today, freq="D")
    template = {
        "Dia": default_index.strftime("%d/%m").tolist(),
        "DiaISO": default_index.strftime("%Y-%m-%d").tolist(),
        label: [0.0] * len(default_index),
    }

    if df.empty or date_col not in df.columns or value_col not in df.columns:
        return template

    dates = pd.to_datetime(df[date_col], errors="coerce")
    values = pd.to_numeric(df[value_col], errors="coerce")
    valid = dates.notna() & values.notna()
    if not valid.any():
        return template

    data = pd.DataFrame({"data": dates.loc[valid].dt.normalize(), "valor": values.loc[valid].astype("float64")})
    data = data.dropna()
    if data.empty:
        return template

    start = start_default
    end = today
    index = default_index
    window_mask = data["data"].between(start, end)
    if not window_mask.any():
        end = data["data"].max()
        if pd.isna(end):
            return template
        start = end - pd.Timedelta(days=6)
        index = pd.date_range(start, end, freq="D")
        template = {"Dia": index.strftime("%d/%m").tolist(), "DiaISO": index.strftime("%Y-%m-%d").tolist(), label: [0.0] * len(index)}
        window_mask = data["data"].between(start, end)
        if not window_mask.any():
            return template

    grouped = data.loc[window_mask].groupby("data")["valor"].sum()
    grouped = grouped.reindex(index, fill_value=0.0).astype("float64")
    return {"Dia": index.strftime("%d/%m").tolist(), "DiaISO": index.strftime("%Y-%m-%d").tolist(), label: grouped.round(2).tolist()}


def _finalize_common(
    df: pd.DataFrame,
    *,
    date_columns: list[str] | None = None,
    numeric_columns: list[str] | None = None,
    text_columns: list[str] | None = None,
    plate_columns: list[str] | None = None,
    default_category: str = "Transporte",
) -> pd.DataFrame:
    df = df.copy()
    for column in date_columns or []:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce")
    for column in numeric_columns or []:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in text_columns or []:
        _normalize_text_column(df, column)
    if "Combustivel" in df.columns:
        _normalize_combustivel_column(df)
    for column in plate_columns or []:
        if column in df.columns:
            df[column] = _normalize_plate_series(df[column])
    _normalize_category_column(df, default=default_category)
    _normalize_mes(df)
    return df


def load_combustivel() -> pd.DataFrame:
    cache = _COMBUSTIVEL_CACHE
    with cache["lock"]:
        version = (_db_version("combustivel"), _db_version("combustivel_km"))
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()

        df = _read_database_table("combustivel", _COMBUSTIVEL_COLUMNS, date_columns=["Data"])
        df = _finalize_common(
            df,
            date_columns=["Data"],
            numeric_columns=["Km Rodados", "Litros", "Custo"],
            text_columns=["Combustivel", "POSTOS"],
            plate_columns=["PLACA"],
        )
        df = _apply_plate_categories(df)

        try:
            km = _read_database_table("combustivel_km", _COMBUSTIVEL_KM_COLUMNS)
            km = _finalize_common(km, numeric_columns=["Km Rodados"], plate_columns=["PLACA"])
            km = km.dropna(subset=["Mes", "Km Rodados"])
        except Exception:
            km = _empty(_COMBUSTIVEL_KM_COLUMNS)
        cache["km_rodados_mensal"] = km[_COMBUSTIVEL_KM_COLUMNS].copy()
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy()


def load_combustivel_km() -> pd.DataFrame:
    cache = _COMBUSTIVEL_KM_CACHE
    with cache["lock"]:
        version = _db_version("combustivel_km")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()
        try:
            km = _read_database_table("combustivel_km", _COMBUSTIVEL_KM_COLUMNS)
            km = _finalize_common(km, numeric_columns=["Km Rodados"], plate_columns=["PLACA"])
            km = km.dropna(subset=["Mes", "PLACA"])
        except Exception:
            km = _empty(_COMBUSTIVEL_KM_COLUMNS)
        km = km[_COMBUSTIVEL_KM_COLUMNS].copy()
        cache["mtime"] = version
        cache["df"] = km.copy()
        return km.copy()


def load_empilhadeira_horas() -> pd.DataFrame:
    cache = _EMPILHADEIRA_HORAS_CACHE
    with cache["lock"]:
        version = _db_version("empilhadeira_horas")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()
        try:
            horas = _read_database_table("empilhadeira_horas", _EMPILHADEIRA_HORAS_COLUMNS)
            horas = _finalize_common(horas, numeric_columns=["Horas"], plate_columns=["PLACA"])
            horas = horas.dropna(subset=["Mes", "PLACA"])
        except Exception:
            horas = _empty(_EMPILHADEIRA_HORAS_COLUMNS)
        horas = horas[_EMPILHADEIRA_HORAS_COLUMNS].copy()
        cache["mtime"] = version
        cache["df"] = horas.copy()
        return horas.copy()


def load_salarios_transporte() -> pd.DataFrame:
    cache = _SALARIOS_TRANSPORTE_CACHE
    with cache["lock"]:
        version = _db_version("salarios_transporte")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()
        try:
            salarios = _read_database_table(
                "salarios_transporte",
                _SALARIOS_TRANSPORTE_COLUMNS,
            )
            salarios = _finalize_common(
                salarios,
                numeric_columns=["Valor"],
            )
            salarios = salarios.dropna(subset=["Mes"])
            salarios["Valor"] = (
                pd.to_numeric(salarios["Valor"], errors="coerce")
                .fillna(0.0)
                .clip(lower=0)
            )
        except Exception:
            salarios = _empty(_SALARIOS_TRANSPORTE_COLUMNS)
        salarios = (
            salarios[_SALARIOS_TRANSPORTE_COLUMNS]
            .sort_values("Mes")
            .reset_index(drop=True)
        )
        cache["mtime"] = version
        cache["df"] = salarios.copy()
        return salarios.copy()


def _load_text_registry(dataset: str, columns: list[str], column: str) -> pd.DataFrame:
    cache = _TEXT_REGISTRY_CACHES.get(dataset)
    version = (_db_version(dataset), _db_version("combustivel"))
    if cache is None:
        cached = None
    else:
        with cache["lock"]:
            cached = cache.get("df")
            if cached is not None and cache.get("mtime") == version:
                return cached.copy()

    frames: list[pd.DataFrame] = []
    try:
        registered = _read_database_table(dataset, columns)
        if column in registered.columns:
            frames.append(registered[[column]].copy())
    except Exception:
        pass

    historical = None
    with _COMBUSTIVEL_CACHE["lock"]:
        combustivel_mtime = _COMBUSTIVEL_CACHE.get("mtime")
        combustivel_df = _COMBUSTIVEL_CACHE.get("df")
        if (
            combustivel_df is not None
            and isinstance(combustivel_mtime, tuple)
            and combustivel_mtime
            and combustivel_mtime[0] == version[1]
        ):
            historical = combustivel_df
    if historical is None:
        try:
            historical = _read_database_table("combustivel", _COMBUSTIVEL_COLUMNS)
        except Exception:
            historical = None
    if historical is not None and column in historical.columns:
        frames.append(historical[[column]].copy())

    if not frames:
        df = _empty(columns)
    else:
        df = pd.concat(frames, ignore_index=True)
        _normalize_text_column(df, column)
        if column == "Combustivel":
            _normalize_combustivel_column(df)
        df = df.dropna(subset=[column]).drop_duplicates(subset=[column], keep="last")
        if df.empty:
            df = _empty(columns)
        else:
            df = df[columns].sort_values(column).reset_index(drop=True)

    if cache is not None:
        with cache["lock"]:
            cache["mtime"] = version
            cache["df"] = df.copy()
    return df.copy()


def load_combustiveis() -> pd.DataFrame:
    return _load_text_registry("combustiveis", _COMBUSTIVEIS_COLUMNS, "Combustivel")


def load_postos() -> pd.DataFrame:
    return _load_text_registry("postos", _POSTOS_COLUMNS, "POSTOS")


def _registry_values(df: pd.DataFrame, column: str, loader) -> list:
    values = set(_unique_sorted(df, column))
    try:
        registry = loader()
    except Exception:
        registry = _empty([column])
    values.update(_unique_sorted(registry, column))
    return sorted(values)


def _pneu_legacy_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty or "OFICINA" not in df.columns:
        return pd.Series(False, index=df.index)
    oficina = df["OFICINA"].astype("string").fillna("").map(_normalize_ascii).str.upper()
    return oficina.str.contains("PNEU", na=False)


def _km_by_month_plate(df: pd.DataFrame) -> dict[tuple[str, str], float]:
    if df is None or df.empty or "Mes" not in df.columns or "Km Rodados" not in df.columns:
        return {}
    columns = ["Mes", "Km Rodados"]
    if "PLACA" in df.columns:
        columns.append("PLACA")
    data = df[columns].copy()
    data["Km Rodados"] = pd.to_numeric(data["Km Rodados"], errors="coerce")
    data = data.dropna(subset=["Mes", "Km Rodados"])
    if data.empty:
        return {}
    if "PLACA" not in data.columns:
        data["PLACA"] = "__GERAL__"
    data["Mes"] = data["Mes"].astype("string").str.strip()
    data["PLACA"] = data["PLACA"].astype("string").fillna("__GERAL__").str.strip()
    grouped = data.groupby(["Mes", "PLACA"], dropna=False)["Km Rodados"].sum()
    return {(str(mes), str(placa)): float(value or 0.0) for (mes, placa), value in grouped.items()}


def _combined_km_metrics(_df: pd.DataFrame, km_override: pd.DataFrame | None = None) -> tuple[float, dict]:
    override_map = _km_by_month_plate(km_override) if km_override is not None else {}
    monthly = defaultdict(float)
    for mes, placa in sorted(override_map):
        monthly[mes] += override_map.get((mes, placa), 0.0)
    total = sum(monthly.values())
    meses = sorted(monthly)
    return total, {"Mes": meses, "Km Rodados": [round(monthly[mes], 3) for mes in meses]}


def agg_combustivel(df: pd.DataFrame, *, km_override: pd.DataFrame | None = None) -> dict:
    custo_total = float(pd.to_numeric(df.get("Custo"), errors="coerce").sum()) if "Custo" in df else 0.0
    km_total, km_mensal = _combined_km_metrics(df, km_override)
    litros_total = float(pd.to_numeric(df.get("Litros"), errors="coerce").sum()) if "Litros" in df else 0.0
    custo_por_km = (custo_total / km_total) if km_total else 0.0
    km_por_litro = (km_total / litros_total) if litros_total else 0.0
    custo_por_litro = (custo_total / litros_total) if litros_total else 0.0
    meses_distintos = df["Mes"].dropna().unique() if "Mes" in df else []
    media_mensal = float(custo_total / len(meses_distintos)) if len(meses_distintos) else 0.0

    return {
        "km_total": km_total,
        "litros_total": litros_total,
        "custo_total": custo_total,
        "media_mensal": media_mensal,
        "custo_por_km": custo_por_km,
        "km_por_litro": km_por_litro,
        "custo_por_litro": custo_por_litro,
        "custo_mensal": _group_sum(df, "Mes", sort_by="group"),
        "km_mensal": km_mensal,
        "litros_mensal": _group_sum(df, "Mes", "Litros", sort_by="group"),
        "gasto_por_posto": _group_sum(df, "POSTOS"),
        "gasto_por_combustivel": _group_sum(df, "Combustivel"),
        "gasto_por_placa": _group_sum(df, "PLACA"),
        "placas": _unique_sorted(df, "PLACA"),
        "postos": _registry_values(df, "POSTOS", load_postos),
        "combustiveis": _registry_values(df, "Combustivel", load_combustiveis),
        "meses": _unique_sorted(df, "Mes"),
        "segmentos": _unique_sorted(df, "Categoria"),
        "gasto_semana": _weekly_series(df, "Data", "Custo", "Custo"),
    }


def load_manutencao() -> pd.DataFrame:
    cache = _MANUTENCAO_CACHE
    with cache["lock"]:
        version = _db_version("manutencao")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()

        df = _read_database_table("manutencao", _MANUTENCAO_COLUMNS, date_columns=["Data"])
        df = _finalize_common(
            df,
            date_columns=["Data"],
            numeric_columns=["Custo"],
            text_columns=["OFICINA"],
            plate_columns=["PLACA"],
        )
        df = _apply_plate_categories(df)
        df = df.loc[~_pneu_legacy_mask(df)].copy()
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy()


def agg_manutencao(df: pd.DataFrame) -> dict:
    custo_total = float(pd.to_numeric(df.get("Custo"), errors="coerce").sum()) if "Custo" in df else 0.0
    total_servicos = int(len(df))
    media_servico = float(custo_total / total_servicos) if total_servicos else 0.0
    meses_distintos = df["Mes"].dropna().unique() if "Mes" in df else []
    media_mensal = float(custo_total / len(meses_distintos)) if len(meses_distintos) else 0.0

    return {
        "custo_total": custo_total,
        "total_servicos": total_servicos,
        "media_servico": media_servico,
        "media_mensal": media_mensal,
        "custo_mensal": _group_sum(df, "Mes", sort_by="group"),
        "gasto_por_placa": _group_sum(df, "PLACA"),
        "gasto_por_oficina": _group_sum(df, "OFICINA"),
        "placas": _unique_sorted(df, "PLACA"),
        "oficinas": _unique_sorted(df, "OFICINA"),
        "meses": _unique_sorted(df, "Mes"),
        "segmentos": _unique_sorted(df, "Categoria"),
        "custo_semana": _weekly_series(df, "Data", "Custo", "Custo"),
    }


def load_pneus() -> pd.DataFrame:
    cache = _PNEUS_CACHE
    with cache["lock"]:
        version = (_db_version("pneus"), _db_version("manutencao"))
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()

        df = _read_database_table("pneus", _PNEUS_COLUMNS, date_columns=["Data"])
        df = _finalize_common(
            df,
            date_columns=["Data"],
            numeric_columns=["Quantidade", "Custo"],
            text_columns=["Fornecedor", "Medida", "Observacao"],
            plate_columns=["PLACA"],
        )
        df = _apply_plate_categories(df)

        try:
            manutencao = _read_database_table("manutencao", _MANUTENCAO_COLUMNS, date_columns=["Data"])
            manutencao = _finalize_common(
                manutencao,
                date_columns=["Data"],
                numeric_columns=["Custo"],
                text_columns=["OFICINA"],
                plate_columns=["PLACA"],
            )
            manutencao = _apply_plate_categories(manutencao)
            legacy = manutencao.loc[_pneu_legacy_mask(manutencao)].copy()
        except Exception:
            legacy = _empty(_MANUTENCAO_COLUMNS)

        if not legacy.empty:
            legacy_pneus = pd.DataFrame(
                {
                    "Data": legacy.get("Data"),
                    "Mes": legacy.get("Mes"),
                    "PLACA": pd.NA,
                    "Categoria": "Transporte",
                    "Fornecedor": legacy.get("OFICINA"),
                    "Quantidade": pd.NA,
                    "Medida": "",
                    "Custo": legacy.get("Custo"),
                    "Observacao": "Origem: manutenção",
                }
            )
            df = pd.concat([df, legacy_pneus[_PNEUS_COLUMNS]], ignore_index=True)

        if not df.empty:
            dedupe_cols = ["Data", "Mes", "Fornecedor", "Custo"]
            df = df.drop_duplicates(subset=[column for column in dedupe_cols if column in df.columns], keep="first")
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy()


def load_hoteis() -> pd.DataFrame:
    cache = _HOTEIS_CACHE
    with cache["lock"]:
        version = _db_version("hoteis")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy()

        df = _read_database_table("hoteis", _HOTEIS_COLUMNS, date_columns=["Data"])
        df = _finalize_common(
            df,
            date_columns=["Data"],
            numeric_columns=["Valor", "Dias"],
            text_columns=["Motorista", "Ajudante", "Cidade", "Hotel", "Tipo"],
        )
        df["Categoria"] = "Transporte"
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy()


def agg_hoteis(df: pd.DataFrame) -> dict:
    reservas = df[df["Data"].notna()].copy() if "Data" in df.columns else df.copy()
    if "Data" in reservas.columns:
        reservas["Data"] = pd.to_datetime(reservas["Data"], errors="coerce")
    valor_total = float(pd.to_numeric(reservas.get("Valor"), errors="coerce").fillna(0).sum()) if "Valor" in reservas else 0.0
    reservas_total = int(reservas.shape[0])
    meses_distintos = reservas["Mes"].dropna().unique() if "Mes" in reservas else []
    media_mensal = float(valor_total / len(meses_distintos)) if len(meses_distintos) else 0.0
    valor_medio_reserva = float(valor_total / reservas_total) if reservas_total else 0.0
    col_nao_planejada = next(
        (
            col
            for col in reservas.columns
            if str(col).strip().upper().replace(" ", "") in {"NAOPLANEJADA", "NAOPLANEJADAS"}
        ),
        None,
    )
    if col_nao_planejada:
        flag_series = pd.to_numeric(reservas[col_nao_planejada], errors="coerce").fillna(0)
        reservas = reservas.assign(_NaoPlanejada=flag_series.astype("int64"))
    else:
        reservas = reservas.assign(_NaoPlanejada=0)

    if "Data" in reservas.columns and "Valor" in reservas.columns:
        mask_sabado = reservas["Data"].dt.dayofweek.isin([4, 5]).fillna(False)
        mask_nao_planejada = reservas["_NaoPlanejada"] == 1
        mask_nao_planejada_total = mask_nao_planejada | mask_sabado
        valor_sabado = float(reservas.loc[mask_sabado, "Valor"].fillna(0).sum())
        valor_nao_planejado = float(reservas.loc[mask_nao_planejada_total, "Valor"].fillna(0).sum())
        reservas_nao_planejadas = int(mask_nao_planejada_total.sum())
    else:
        valor_sabado = 0.0
        valor_nao_planejado = 0.0
        reservas_nao_planejadas = 0

    semanal = _weekly_series(reservas, "Data", "Valor", "Valor")
    if "DiaISO" in semanal:
        nao_planejada_por_dia = (
            reservas.loc[reservas["_NaoPlanejada"] == 1, "Data"].dt.normalize().value_counts()
            if "Data" in reservas.columns
            else pd.Series(dtype="int64")
        )
        semanal["NaoPlanejada"] = [
            bool(nao_planejada_por_dia.get(pd.to_datetime(iso, errors="coerce").normalize(), 0))
            if pd.notna(pd.to_datetime(iso, errors="coerce"))
            else False
            for iso in semanal["DiaISO"]
        ]
    else:
        semanal["NaoPlanejada"] = []

    return {
        "valor_total": valor_total,
        "reservas_total": reservas_total,
        "media_mensal": media_mensal,
        "valor_medio_reserva": valor_medio_reserva,
        "valor_mensal": _group_sum(reservas, "Mes", "Valor", sort_by="group"),
        "valor_por_cidade": _group_sum(reservas, "Cidade", "Valor"),
        "valor_por_hotel": _group_sum(reservas, "Hotel", "Valor"),
        "meses": _unique_sorted(reservas, "Mes"),
        "cidades": _unique_sorted(reservas, "Cidade"),
        "hoteis": _unique_sorted(reservas, "Hotel"),
        "valor_semana": semanal,
        "valor_sabado": valor_sabado,
        "valor_nao_planejado": valor_nao_planejado,
        "reservas_nao_planejadas": reservas_nao_planejadas,
    }


def load_pedagio() -> pd.DataFrame:
    cache = _PEDAGIO_CACHE
    with cache["lock"]:
        version = _db_version("pedagio")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy(deep=False)

        df = _read_database_table("pedagio", _PEDAGIO_COLUMNS, date_columns=["Data"])
        df = _finalize_common(
            df,
            date_columns=["Data"],
            numeric_columns=["Custo"],
            text_columns=["Tipo"],
            plate_columns=["PLACA"],
        )
        df = _apply_plate_categories(df)
        if "Tipo" in df.columns:
            df["Tipo"] = df["Tipo"].apply(_normalize_tipo_value).astype("string")
        df["Tipo"] = df["Tipo"].fillna("Outros")
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy(deep=False)


def load_peso() -> pd.DataFrame:
    cache = _PESO_CACHE
    with cache["lock"]:
        version = _db_version("peso")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy(deep=False)

        df = _read_database_table("peso", _PESO_COLUMNS, date_columns=["Data"])
        df = _finalize_common(
            df,
            date_columns=["Data"],
            numeric_columns=["Peso", "Valor"],
            text_columns=["Cidade", "Rota"],
            plate_columns=["PLACA"],
        )
        df = _apply_plate_categories(df)
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy(deep=False)


def load_rodagem_rota() -> pd.DataFrame:
    cache = _RODAGEM_ROTA_CACHE
    with cache["lock"]:
        version = _db_version("rodagem_rota")
        cached = cache.get("df")
        if cached is not None and cache.get("mtime") == version:
            return cached.copy(deep=False)

        df = _read_database_table("rodagem_rota", _RODAGEM_ROTA_COLUMNS)
        df = _finalize_common(
            df,
            numeric_columns=["Km Rodados"],
            text_columns=["Rota"],
            plate_columns=["PLACA"],
        )
        df = _apply_plate_categories(df)
        if "Categoria" not in df.columns:
            df["Categoria"] = "Transporte"
        if "Rota" in df.columns:
            df["Rota"] = df["Rota"].astype("string").fillna("").str.strip().str.upper()
        cache["mtime"] = version
        cache["df"] = df.copy()
        return df.copy(deep=False)


def agg_peso(df: pd.DataFrame) -> dict:
    peso_total = float(pd.to_numeric(df.get("Peso"), errors="coerce").sum()) if "Peso" in df else 0.0
    valor_total = float(pd.to_numeric(df.get("Valor"), errors="coerce").sum()) if "Valor" in df else 0.0
    return {"peso_total": round(peso_total, 3), "valor_total": round(valor_total, 2)}


def agg_pedagio(df: pd.DataFrame) -> dict:
    registros = df.shape[0]
    if df.empty or "Custo" not in df:
        seguro_df = pd.DataFrame()
        other_df = df
        seguro_total = 0.0
        seguro_por_placa: dict[str, float] = {}
        seguro_por_categoria: dict[str, float] = {}
    else:
        tipo_series = df["Tipo"].astype("string").fillna("") if "Tipo" in df.columns else pd.Series("", index=df.index)
        seguro_mask = tipo_series == "Seguro"
        seguro_df = df[seguro_mask].copy()
        other_df = df[~seguro_mask].copy()
        seguro_total = 0.0
        seguro_por_placa = {}
        seguro_por_categoria = {}
        if not seguro_df.empty:
            group_columns = [column for column in ["PLACA", "Categoria"] if column in seguro_df.columns]
            if not group_columns:
                group_columns = ["Tipo"]
            for keys, group in seguro_df.groupby(group_columns, dropna=False):
                if not isinstance(keys, tuple):
                    keys = (keys,)
                monthly_totals = (
                    pd.to_numeric(group.get("Custo"), errors="coerce")
                    .groupby(group["Mes"].astype("string").fillna("") if "Mes" in group.columns else pd.Series("", index=group.index))
                    .sum()
                )
                value = float(monthly_totals.mean()) if len(monthly_totals) > 1 else float(monthly_totals.sum())
                seguro_total += value
                key_map = dict(zip(group_columns, keys))
                placa = str(key_map.get("PLACA") or "").strip()
                categoria = str(key_map.get("Categoria") or "").strip()
                if placa:
                    seguro_por_placa[placa] = seguro_por_placa.get(placa, 0.0) + value
                if categoria:
                    seguro_por_categoria[categoria] = seguro_por_categoria.get(categoria, 0.0) + value

    other_total = float(pd.to_numeric(other_df.get("Custo"), errors="coerce").sum()) if "Custo" in other_df else 0.0
    custo_total = other_total + seguro_total
    meses_distintos = df["Mes"].dropna().unique() if "Mes" in df else []
    media_mensal = float(custo_total / len(meses_distintos)) if len(meses_distintos) else 0.0
    media_valores = float(custo_total / registros) if registros else 0.0

    tipo_totais = other_df.groupby("Tipo", dropna=False)["Custo"].sum() if "Tipo" in other_df.columns and not other_df.empty else pd.Series(dtype="float64")
    if not seguro_df.empty:
        tipo_totais.loc["Seguro"] = seguro_total
    tipo_contagens = df.groupby("Tipo", dropna=False).size() if "Tipo" in df.columns and not df.empty else pd.Series(dtype="int64")
    gasto_por_placa = _group_sum(other_df, "PLACA", "Custo") if not other_df.empty else {"PLACA": [], "Custo": []}
    if seguro_por_placa:
        placa_totals = dict(zip(gasto_por_placa.get("PLACA", []), gasto_por_placa.get("Custo", [])))
        for placa, value in seguro_por_placa.items():
            placa_totals[placa] = placa_totals.get(placa, 0.0) + value
        placa_items = sorted(placa_totals.items(), key=lambda item: item[1], reverse=True)
        gasto_por_placa = {"PLACA": [item[0] for item in placa_items], "Custo": [item[1] for item in placa_items]}
    seguro_placa_items = sorted(seguro_por_placa.items(), key=lambda item: item[1], reverse=True)

    tipo_items = sorted(tipo_totais.to_dict().items(), key=lambda item: item[1], reverse=True)
    resultado = {
        "custo_total": custo_total,
        "total_lancamentos": registros,
        "media_mensal": media_mensal,
        "ticket_medio": media_valores,
        "media_valores": media_valores,
        "gasto_pedagio": float(tipo_totais.get("Pedagio", 0.0)),
        "gasto_ipva": float(tipo_totais.get("IPVA", 0.0)),
        "gasto_seguro": float(tipo_totais.get("Seguro", 0.0)),
        "qtd_pedagio": int(tipo_contagens.get("Pedagio", 0)),
        "qtd_ipva": int(tipo_contagens.get("IPVA", 0)),
        "qtd_seguro": int(tipo_contagens.get("Seguro", 0)),
        "custo_mensal": _group_sum(other_df, "Mes", "Custo", sort_by="group"),
        "gasto_por_tipo": {"Tipo": [item[0] for item in tipo_items], "Custo": [item[1] for item in tipo_items]},
        "gasto_por_placa": gasto_por_placa,
        "seguro_por_placa": {"PLACA": [item[0] for item in seguro_placa_items], "Custo": [item[1] for item in seguro_placa_items]},
        "meses": _unique_sorted(df, "Mes"),
        "tipos": _unique_sorted(df, "Tipo"),
        "placas": _unique_sorted(df, "PLACA"),
        "custo_semana": _weekly_series(other_df, "Data", "Custo", "Custo"),
    }
    if "Categoria" in df.columns:
        gasto_por_categoria = _group_sum(other_df, "Categoria", "Custo") if not other_df.empty else {"Categoria": [], "Custo": []}
        if seguro_por_categoria:
            category_totals = dict(zip(gasto_por_categoria.get("Categoria", []), gasto_por_categoria.get("Custo", [])))
            for categoria, value in seguro_por_categoria.items():
                category_totals[categoria] = category_totals.get(categoria, 0.0) + value
            category_items = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)
            gasto_por_categoria = {"Categoria": [item[0] for item in category_items], "Custo": [item[1] for item in category_items]}
        resultado["segmentos"] = _unique_sorted(df, "Categoria")
        resultado["gasto_por_categoria"] = gasto_por_categoria
    else:
        resultado["segmentos"] = []
        resultado["gasto_por_categoria"] = {"Categoria": [], "Custo": []}
    return resultado


def data_comb(params: dict | None = None) -> dict:
    params = params or {}
    df = _apply_plate_categories(load_combustivel())
    km_rodados = _apply_plate_categories(load_combustivel_km())

    ano = _parse_int(_param(params, "ano"))
    meses = _parse_mes_list(params.get("mes"))
    categoria = _param(params, "categoria") or _param(params, "segmento") or "Transporte"
    placa = _param(params, "placa")
    posto = _param(params, "posto")
    combustivel = _param(params, "combustivel")

    segmentos_disponiveis = _unique_sorted(df, "Categoria")
    df = _filter_category_param(df, categoria)
    km_rodados = _filter_category_param(km_rodados, categoria)

    df = _filter_plate_param(df, placa)
    if posto and posto != "Todos":
        df = df[df["POSTOS"] == posto]
    if combustivel and combustivel != "Todos":
        df = df[df["Combustivel"] == combustivel]

    anos_disponiveis = sorted({*_unique_years(df), *df.attrs.get("anos_sheets", [])})
    df_meses = _filter_by_period(df, ano=ano) if ano is not None else df
    meses_disponiveis = _unique_sorted(df_meses, "Mes")

    if ano is not None:
        df = _filter_by_period(df, ano=ano)
    if meses:
        df = df[df["Mes"].isin(meses)]

    km_override = None
    if isinstance(km_rodados, pd.DataFrame) and not km_rodados.empty:
        km_override = km_rodados.copy()
        if placa and placa != "Todos":
            km_override = km_override[km_override["PLACA"] == _normalize_plate_value(placa)]
        if ano is not None:
            km_override = km_override[pd.to_datetime(km_override["Mes"], errors="coerce").dt.year == ano]
        if meses:
            km_override = km_override[km_override["Mes"].isin(meses)]
        if km_override.empty:
            km_override = None

    resultado = agg_combustivel(df, km_override=km_override)
    resultado["anos"] = anos_disponiveis
    resultado["meses"] = meses_disponiveis
    resultado["segmentos"] = segmentos_disponiveis
    return resultado


def data_manu(params: dict | None = None) -> dict:
    params = params or {}
    df = _exclude_vex(load_manutencao())
    segmentos_disponiveis = _unique_sorted(df, "Categoria")

    ano = _parse_int(_param(params, "ano"))
    meses = _parse_mes_list(params.get("mes"))
    placa = _param(params, "placa")
    oficina = _param(params, "oficina")
    segmento = _param(params, "segmento") or _param(params, "categoria")

    df = _filter_plate_param(df, placa)
    if oficina and oficina != "Todos":
        df = df[df["OFICINA"] == oficina]
    df = _filter_category_param(df, segmento)

    anos_disponiveis = sorted({*_unique_years(df), *df.attrs.get("anos_sheets", [])})
    df_meses = _filter_by_period(df, ano=ano) if ano is not None else df
    meses_disponiveis = _unique_sorted(df_meses, "Mes")

    if ano is not None:
        df = _filter_by_period(df, ano=ano)
    if meses:
        df = df[df["Mes"].isin(meses)]

    resultado = agg_manutencao(df)
    resultado["anos"] = anos_disponiveis
    resultado["meses"] = meses_disponiveis
    resultado["segmentos"] = segmentos_disponiveis
    return resultado


def data_hoteis(params: dict | None = None) -> dict:
    params = params or {}
    df_total = _exclude_vex(load_hoteis())
    totais_gerais = agg_hoteis(df_total)
    df = df_total.copy()
    segmentos_disponiveis = _unique_sorted(df, "Categoria")

    ano = _parse_int(_param(params, "ano"))
    meses = _parse_mes_list(params.get("mes"))
    segmento = _param(params, "segmento") or _param(params, "categoria")
    cidade = _param(params, "cidade")
    hotel = _param(params, "hotel")

    df = _filter_category_param(df, segmento)
    if cidade and cidade != "Todos":
        df = df[df["Cidade"] == cidade]
    if hotel and hotel != "Todos":
        df = df[df["Hotel"] == hotel]

    anos_disponiveis = sorted({*_unique_years(df), *df.attrs.get("anos_sheets", [])})
    df_meses = _filter_by_period(df, ano=ano) if ano is not None else df
    meses_disponiveis = _unique_sorted(df_meses, "Mes")

    if ano is not None:
        df = _filter_by_period(df, ano=ano)
    if meses:
        df = df[df["Mes"].isin(meses)]

    resultado = agg_hoteis(df)
    resultado["valor_sabado_total"] = totais_gerais.get("valor_sabado", 0.0)
    resultado["valor_nao_planejado_total"] = totais_gerais.get("valor_nao_planejado", 0.0)
    resultado["anos"] = anos_disponiveis
    resultado["meses"] = meses_disponiveis
    resultado["segmentos"] = segmentos_disponiveis
    return resultado


def data_pedagio(params: dict | None = None) -> dict:
    params = params or {}
    df = _exclude_vex(load_pedagio())
    segmentos_disponiveis = _unique_sorted(df, "Categoria")

    ano = _parse_int(_param(params, "ano"))
    meses = _parse_mes_list(params.get("mes"))
    placa = _param(params, "placa")
    tipo = _param(params, "tipo")
    segmento = _param(params, "segmento") or _param(params, "categoria")

    df = _filter_plate_param(df, placa)
    if tipo and tipo != "Todos":
        df = df[df["Tipo"] == _normalize_tipo_value(tipo)]
    df = _filter_category_param(df, segmento)

    anos_disponiveis = sorted({*_unique_years(df), *df.attrs.get("anos_sheets", [])})
    df_meses = _filter_by_period(df, ano=ano) if ano is not None else df
    meses_disponiveis = _unique_sorted(df_meses, "Mes")

    if ano is not None:
        df = _filter_by_period(df, ano=ano)
    if meses:
        df = df[df["Mes"].isin(meses)]

    resultado = agg_pedagio(df)
    resultado["anos"] = anos_disponiveis
    resultado["meses"] = meses_disponiveis
    resultado["segmentos"] = segmentos_disponiveis
    return resultado


def data_vex(params: dict | None = None) -> dict:
    params = params or {}
    ano = _parse_int(_param(params, "ano"))
    meses = _parse_mes_list(params.get("mes"))
    placa = _param(params, "placa")

    df_comb = _only_registered_category(load_combustivel(), "Vex")
    df_manu = _only_registered_category(load_manutencao(), "Vex")
    df_hoteis = load_hoteis().iloc[0:0].copy()
    df_ped = _only_registered_category(load_pedagio(), "Vex")
    km_rodados = _only_registered_category(_apply_plate_categories(load_combustivel_km()), "Vex")

    anos_disponiveis: set[int] = set()
    for df_src in (df_comb, df_manu, df_hoteis, df_ped, km_rodados):
        anos_disponiveis.update(_unique_years(df_src))
        anos_disponiveis.update(df_src.attrs.get("anos_sheets", []))

    def _meses_disponiveis(*dfs: pd.DataFrame) -> list[str]:
        frames = [df for df in dfs if not df.empty and "Mes" in df.columns]
        if not frames:
            return []
        merged = pd.concat(frames, ignore_index=True)
        return _unique_sorted(merged, "Mes")

    df_meses_base = [df_comb, df_manu, df_hoteis, df_ped, km_rodados]
    if ano is not None:
        df_meses_base = [_filter_by_period(df, ano=ano) for df in df_meses_base]
    meses_disponiveis = _meses_disponiveis(*df_meses_base)

    df_placas_base = [df_comb, df_manu, df_ped, km_rodados]
    if ano is not None:
        df_placas_base = [_filter_by_period(df, ano=ano) for df in df_placas_base]
    if meses:
        df_placas_base = [df[df["Mes"].isin(meses)] for df in df_placas_base]
    frames = [df[["PLACA"]] for df in df_placas_base if "PLACA" in df.columns]
    placas_disponiveis = _unique_sorted(pd.concat(frames, ignore_index=True), "PLACA") if frames else []

    def _apply_filters(df: pd.DataFrame) -> pd.DataFrame:
        if ano is not None:
            df = _filter_by_period(df, ano=ano)
        if meses:
            df = df[df["Mes"].isin(meses)]
        if placa and placa != "Todos" and "PLACA" in df.columns:
            df = df[df["PLACA"] == _normalize_plate_value(placa)]
        return df

    df_comb = _apply_filters(df_comb)
    df_manu = _apply_filters(df_manu)
    df_hoteis = _apply_filters(df_hoteis)
    df_ped = _apply_filters(df_ped)

    km_override = None
    if isinstance(km_rodados, pd.DataFrame) and not km_rodados.empty:
        km_override = km_rodados.copy()
        if ano is not None:
            km_override = km_override[pd.to_datetime(km_override["Mes"], errors="coerce").dt.year == ano]
        if meses:
            km_override = km_override[km_override["Mes"].isin(meses)]
        if placa and placa != "Todos":
            km_override = km_override[km_override["PLACA"] == _normalize_plate_value(placa)]
        if km_override.empty:
            km_override = None

    total_comb = float(pd.to_numeric(df_comb.get("Custo"), errors="coerce").sum()) if "Custo" in df_comb else 0.0
    if km_override is not None and "Km Rodados" in km_override.columns:
        km_total = float(pd.to_numeric(km_override["Km Rodados"], errors="coerce").sum())
    else:
        km_total = 0.0
    litros_total = float(pd.to_numeric(df_comb.get("Litros"), errors="coerce").sum()) if "Litros" in df_comb else 0.0
    total_manu = float(pd.to_numeric(df_manu.get("Custo"), errors="coerce").sum()) if "Custo" in df_manu else 0.0
    total_hoteis = 0.0
    total_ped = float(pd.to_numeric(df_ped.get("Custo"), errors="coerce").sum()) if "Custo" in df_ped else 0.0
    total_vex = total_comb + total_manu + total_hoteis + total_ped

    monthly_map: dict[str, float] = {}
    for src, key in (
        (_group_sum(df_comb, "Mes", "Custo", sort_by="group"), "Custo"),
        (_group_sum(df_manu, "Mes", "Custo", sort_by="group"), "Custo"),
        (_group_sum(df_ped, "Mes", "Custo", sort_by="group"), "Custo"),
    ):
        for mes_val, valor in zip(src.get("Mes", []), src.get(key, [])):
            monthly_map[mes_val] = monthly_map.get(mes_val, 0.0) + float(valor or 0)

    placa_totais: dict[str, float] = {}
    for df_src, col_valor in ((df_comb, "Custo"), (df_manu, "Custo"), (df_ped, "Custo")):
        if df_src.empty or "PLACA" not in df_src.columns or col_valor not in df_src.columns:
            continue
        df_val = df_src.dropna(subset=["PLACA"]).copy()
        df_val[col_valor] = pd.to_numeric(df_val[col_valor], errors="coerce")
        for placa_val, total in df_val.groupby("PLACA")[col_valor].sum().items():
            key = str(placa_val).strip()
            placa_totais[key] = placa_totais.get(key, 0.0) + float(total or 0.0)

    placas_ordenadas = sorted(placa_totais.items(), key=lambda item: item[1], reverse=True)
    meses_sorted = sorted(monthly_map.keys())
    km_mensal = _ranking_monthly_km(km_override if km_override is not None else _empty(_COMBUSTIVEL_KM_COLUMNS), df_comb)
    litros_mensal = _group_sum(df_comb, "Mes", "Litros", sort_by="group")
    return {
        "anos": sorted(anos_disponiveis),
        "meses": meses_disponiveis,
        "placas": placas_disponiveis,
        "total_vex": round(total_vex, 2),
        "combustivel_total": round(total_comb, 2),
        "manutencao_total": round(total_manu, 2),
        "hoteis_total": round(total_hoteis, 2),
        "pedagio_total": round(total_ped, 2),
        "km_total": round(km_total, 2),
        "litros_total": round(litros_total, 2),
        "km_por_litro": round((km_total / litros_total) if litros_total else 0.0, 3),
        "custo_por_km": round((total_comb / km_total) if km_total else 0.0, 4),
        "custo_por_litro": round((total_comb / litros_total) if litros_total else 0.0, 4),
        "mensal_total": {"Mes": meses_sorted, "Valor": [round(monthly_map[mes], 2) for mes in meses_sorted]},
        "km_mensal": km_mensal,
        "litros_mensal": litros_mensal,
        "por_area": {"Area": ["Combustivel", "Manutencao", "Pedagio"], "Valor": [round(total_comb, 2), round(total_manu, 2), round(total_ped, 2)]},
        "gasto_por_placa": {"PLACA": [item[0] for item in placas_ordenadas], "Valor": [round(item[1], 2) for item in placas_ordenadas]},
    }


def _ranking_parse_category_list(raw) -> list[str]:
    categorias: list[str] = []
    for value in _as_list(raw):
        if value in (None, "", "Todos"):
            continue
        for part in str(value).split(","):
            categoria = part.strip()
            if not categoria or categoria.lower() == "todos":
                continue
            normalized = _normalize_category_value(categoria)
            if normalized not in categorias:
                categorias.append(normalized)
    return categorias


def _ranking_filter_categories(df: pd.DataFrame, categorias: list[str]) -> pd.DataFrame:
    if not categorias:
        return df
    if df.empty or "Categoria" not in df.columns:
        return df.iloc[0:0].copy()
    targets = {categoria.lower() for categoria in categorias}
    mask = _normalize_categoria(df["Categoria"]).isin(targets)
    return df.loc[mask].copy()


def _truthy_param(value) -> bool:
    raw = _param({"value": value}, "value")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "sim", "yes", "on"}


def _ranking_valid_plate(value) -> bool:
    if pd.isna(value):
        return False
    text = str(value).strip().upper()
    return text != "SEM PLACA" and _is_plate_identifier(text)


def _ranking_filter_valid_plates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "PLACA" not in df.columns:
        return df.iloc[0:0].copy()
    return df[df["PLACA"].apply(_ranking_valid_plate)].copy()


def _ranking_parse_plate_list(raw) -> list[str]:
    plates: list[str] = []
    for value in _as_list(raw):
        if value in (None, "", "Todos"):
            continue
        for part in str(value).split(","):
            plate = _normalize_plate_value(part.strip())
            if _ranking_valid_plate(plate):
                plates.append(str(plate))
    return sorted(set(plates))


def _ranking_filter_plates(df: pd.DataFrame, placas: list[str]) -> pd.DataFrame:
    if not placas:
        return df
    if df.empty or "PLACA" not in df.columns:
        return df.iloc[0:0].copy()
    return df[df["PLACA"].isin(placas)].copy()


def _ranking_parse_route_list(raw) -> list[str]:
    routes: list[str] = []
    seen: set[str] = set()
    for value in _as_list(raw):
        if value in (None, "", "Todos"):
            continue
        for part in str(value).split(","):
            route = part.strip()
            key = route.casefold()
            if not route or key == "todos" or key in seen:
                continue
            seen.add(key)
            routes.append(route)
    return routes


_RANKING_NO_ROUTE_LABEL = "Sem rota"


def _ranking_route_labels(df: pd.DataFrame) -> pd.Series:
    if df.empty or "Rota" not in df.columns:
        return pd.Series(_RANKING_NO_ROUTE_LABEL, index=df.index, dtype="string")
    routes = df["Rota"].astype("string").fillna("").str.strip()
    return routes.mask(routes.eq(""), _RANKING_NO_ROUTE_LABEL)


def _ranking_filter_routes(df: pd.DataFrame, routes: list[str]) -> pd.DataFrame:
    if not routes:
        return df
    if df.empty or "Rota" not in df.columns:
        return df.iloc[0:0].copy()
    targets = {route.casefold() for route in routes}
    normalized = _ranking_route_labels(df).str.casefold()
    return df.loc[normalized.isin(targets)].copy()


def _ranking_eligible_route_weight_rows(
    df_peso: pd.DataFrame,
    df_route_km: pd.DataFrame,
) -> pd.DataFrame:
    """Mantem Freteiros e placas com rodagem cadastrada na rota e no mes."""
    required = {"Mes", "Rota", "PLACA", "Categoria"}
    if df_peso.empty or not required.issubset(df_peso.columns):
        return df_peso.iloc[0:0].copy()

    work = df_peso.copy()
    work["__Route"] = work["Rota"].astype("string").fillna("").str.strip().str.casefold()
    work["__Month"] = work["Mes"].astype("string").fillna("").str.strip()
    work["__Plate"] = work["PLACA"].astype("string").fillna("").str.strip()
    is_freighter = _normalize_categoria(work["Categoria"]).eq("freteiro")

    registered_keys: set[tuple[str, str, str]] = set()
    route_required = {"Mes", "Rota", "PLACA", "Km Rodados"}
    if not df_route_km.empty and route_required.issubset(df_route_km.columns):
        registered = df_route_km[["Mes", "Rota", "PLACA", "Km Rodados"]].copy()
        registered["Km Rodados"] = pd.to_numeric(
            registered["Km Rodados"], errors="coerce"
        ).fillna(0.0)
        registered = registered[registered["Km Rodados"] > 0]
        registered_keys = {
            (str(month).strip(), str(route).strip().casefold(), str(plate).strip())
            for month, route, plate in registered[["Mes", "Rota", "PLACA"]].itertuples(index=False)
            if str(month).strip() and str(route).strip() and str(plate).strip()
        }

    row_keys = pd.Series(
        list(zip(work["__Month"], work["__Route"], work["__Plate"])),
        index=work.index,
        dtype="object",
    )
    has_registered_mileage = row_keys.isin(registered_keys)
    has_route = work["__Route"].ne("")
    return work.loc[has_route & (is_freighter | has_registered_mileage)].drop(
        columns=["__Route", "__Month", "__Plate"]
    )


def _ranking_filter_route_day_rows(df: pd.DataFrame, route_weight_rows: pd.DataFrame) -> pd.DataFrame:
    """Mantém lançamentos ocorridos na mesma data e placa dos pesos da rota."""
    required = {"Data", "PLACA"}
    if df.empty or route_weight_rows.empty:
        return df.iloc[0:0].copy()
    if not required.issubset(df.columns) or not required.issubset(route_weight_rows.columns):
        return df.iloc[0:0].copy()

    route_days = route_weight_rows[["Data", "PLACA"]].copy()
    route_days["__RouteDate"] = pd.to_datetime(route_days["Data"], errors="coerce").dt.normalize()
    route_days["__RoutePlate"] = route_days["PLACA"].astype("string").str.strip()
    route_days = (
        route_days.dropna(subset=["__RouteDate", "__RoutePlate"])
        .loc[lambda frame: frame["__RoutePlate"] != "", ["__RouteDate", "__RoutePlate"]]
        .drop_duplicates()
    )
    if route_days.empty:
        return df.iloc[0:0].copy()

    work = df.copy()
    work["__RouteDate"] = pd.to_datetime(work["Data"], errors="coerce").dt.normalize()
    work["__RoutePlate"] = work["PLACA"].astype("string").str.strip()
    matched = work.merge(route_days, on=["__RouteDate", "__RoutePlate"], how="inner", sort=False)
    return matched.drop(columns=["__RouteDate", "__RoutePlate"])


def _ranking_route_fuel_costs(
    df_route_km: pd.DataFrame,
    df_comb: pd.DataFrame,
    df_km: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Estima combustível da rota pelo custo/km mensal geral de cada placa."""
    columns = [
        "Mes",
        "Rota",
        "PLACA",
        "Km Rota",
        "Combustivel Mes",
        "Km Geral Mes",
        "Custo por Km",
        "Custo",
    ]
    required = {"Mes", "Rota", "PLACA", "Km Rodados"}
    if df_route_km.empty or not required.issubset(df_route_km.columns):
        return pd.DataFrame(columns=columns), []

    route_km = df_route_km[["Mes", "Rota", "PLACA", "Km Rodados"]].copy()
    route_km["Mes"] = route_km["Mes"].astype("string").str.strip()
    route_km["Rota"] = route_km["Rota"].astype("string").fillna("").str.strip().str.upper()
    route_km["PLACA"] = route_km["PLACA"].astype("string").fillna("").str.strip()
    route_km["Km Rodados"] = pd.to_numeric(route_km["Km Rodados"], errors="coerce").fillna(0.0).clip(lower=0)
    route_km = route_km[
        (route_km["Mes"] != "")
        & (route_km["Rota"] != "")
        & (route_km["PLACA"] != "")
        & (route_km["Km Rodados"] > 0)
    ]
    route_km = route_km.groupby(["Mes", "Rota", "PLACA"], as_index=False)["Km Rodados"].sum()
    if route_km.empty:
        return pd.DataFrame(columns=columns), []

    def _monthly_plate_sum(df: pd.DataFrame, value_column: str, output_column: str) -> pd.DataFrame:
        if df.empty or not {"Mes", "PLACA", value_column}.issubset(df.columns):
            return pd.DataFrame(columns=["Mes", "PLACA", output_column])
        work = df[["Mes", "PLACA", value_column]].copy()
        work["Mes"] = work["Mes"].astype("string").str.strip()
        work["PLACA"] = work["PLACA"].astype("string").fillna("").str.strip()
        work[value_column] = pd.to_numeric(work[value_column], errors="coerce").fillna(0.0).clip(lower=0)
        work = work[(work["Mes"] != "") & (work["PLACA"] != "")]
        return (
            work.groupby(["Mes", "PLACA"], as_index=False)[value_column]
            .sum()
            .rename(columns={value_column: output_column})
        )

    fuel = _monthly_plate_sum(df_comb, "Custo", "Combustivel Mes")
    general_km = _monthly_plate_sum(df_km, "Km Rodados", "Km Geral Mes")
    result = route_km.rename(columns={"Km Rodados": "Km Rota"})
    result = result.merge(fuel, on=["Mes", "PLACA"], how="left")
    result = result.merge(general_km, on=["Mes", "PLACA"], how="left")
    result["Combustivel Mes"] = result["Combustivel Mes"].fillna(0.0)
    result["Km Geral Mes"] = result["Km Geral Mes"].fillna(0.0)
    valid_km = result["Km Geral Mes"] > 0
    result["Custo por Km"] = 0.0
    result.loc[valid_km, "Custo por Km"] = (
        result.loc[valid_km, "Combustivel Mes"] / result.loc[valid_km, "Km Geral Mes"]
    )
    result["Custo"] = result["Custo por Km"] * result["Km Rota"]

    warnings: list[str] = []
    for row in result.loc[~valid_km].itertuples(index=False):
        warnings.append(
            f"{row.Mes} - {row.PLACA} - {row.Rota}: sem KM mensal geral para calcular o combustível."
        )
    missing_fuel = valid_km & (result["Combustivel Mes"] <= 0)
    for row in result.loc[missing_fuel].itertuples(index=False):
        warnings.append(
            f"{row.Mes} - {row.PLACA} - {row.Rota}: sem custo de combustível no mês."
        )

    allocated = result.groupby(["Mes", "PLACA"], as_index=False)["Km Rota"].sum()
    allocated = allocated.merge(general_km, on=["Mes", "PLACA"], how="left")
    allocated["Km Geral Mes"] = allocated["Km Geral Mes"].fillna(0.0)
    for _, row in allocated.loc[
        (allocated["Km Geral Mes"] > 0) & (allocated["Km Rota"] > allocated["Km Geral Mes"] + 0.001)
    ].iterrows():
        warnings.append(
            f"{row['Mes']} - {row['PLACA']}: KM das rotas ({row['Km Rota']:.0f}) "
            f"maior que o KM geral do mês ({row['Km Geral Mes']:.0f})."
        )

    return result[columns].sort_values(["Mes", "Rota", "PLACA"]).reset_index(drop=True), warnings


def _ranking_sum_by_plate(df: pd.DataFrame, value_col: str) -> dict[str, float]:
    if df.empty or "PLACA" not in df.columns or value_col not in df.columns:
        return {}
    data = df.dropna(subset=["PLACA"]).copy()
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce").fillna(0)
    grouped = data.groupby("PLACA")[value_col].sum()
    return {str(placa): float(valor or 0) for placa, valor in grouped.items()}


def _ranking_count_by_plate(df: pd.DataFrame) -> dict[str, int]:
    if df.empty or "PLACA" not in df.columns:
        return {}
    data = df.dropna(subset=["PLACA"])
    if "_RouteSourceId" in data.columns:
        grouped = data.groupby("PLACA")["_RouteSourceId"].nunique()
    else:
        grouped = data.groupby("PLACA").size()
    return {str(placa): int(valor or 0) for placa, valor in grouped.items()}


def _ranking_active_days_by_plate(df_peso: pd.DataFrame) -> dict[str, int]:
    if df_peso.empty or not {"Data", "PLACA"}.issubset(df_peso.columns):
        return {}
    work = df_peso[["Data", "PLACA"]].copy()
    work["Data"] = pd.to_datetime(work["Data"], errors="coerce").dt.normalize()
    work["PLACA"] = work["PLACA"].astype("string").str.strip()
    work = work.dropna(subset=["Data", "PLACA"])
    work = work[work["PLACA"] != ""].drop_duplicates(["Data", "PLACA"])
    if work.empty:
        return {}
    grouped = work.groupby("PLACA")["Data"].nunique()
    return {str(placa): int(days or 0) for placa, days in grouped.items()}


def _ranking_daily_average_costs(
    df_peso: pd.DataFrame,
    daily_average: float,
    value_column: str,
) -> pd.DataFrame:
    columns = ["Data", "Mes", "PLACA", value_column]
    if df_peso.empty or not {"Data", "PLACA"}.issubset(df_peso.columns):
        return pd.DataFrame(columns=columns)

    work = df_peso[["Data", "PLACA"]].copy()
    work["Data"] = pd.to_datetime(work["Data"], errors="coerce").dt.normalize()
    work["PLACA"] = work["PLACA"].astype("string").str.strip()
    work = work.dropna(subset=["Data", "PLACA"])
    work = work[work["PLACA"] != ""].drop_duplicates(["Data", "PLACA"])
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["Mes"] = work["Data"].dt.to_period("M").astype("string")
    work[value_column] = max(float(daily_average or 0.0), 0.0)
    return work[columns].sort_values(["Data", "PLACA"]).reset_index(drop=True)


def _ranking_daily_plate_costs(
    df_peso: pd.DataFrame,
    daily_averages: dict[str, float],
    value_column: str,
) -> pd.DataFrame:
    work = _ranking_daily_average_costs(df_peso, 0.0, value_column)
    if work.empty:
        return work
    work[value_column] = (
        work["PLACA"]
        .map(daily_averages)
        .fillna(0.0)
        .astype("float64")
        .clip(lower=0)
    )
    return work


def _ranking_monthly_shared_costs(
    df_peso_selected: pd.DataFrame,
    df_peso_general: pd.DataFrame,
    monthly_costs: pd.DataFrame,
    *,
    source_column: str = "Valor",
    value_column: str = "Custo",
) -> pd.DataFrame:
    columns = ["Data", "Mes", "PLACA", value_column]
    if monthly_costs.empty or "Mes" not in monthly_costs.columns or source_column not in monthly_costs.columns:
        return pd.DataFrame(columns=columns)

    general_days = _ranking_daily_average_costs(
        df_peso_general,
        0.0,
        value_column,
    )
    selected_days = _ranking_daily_average_costs(
        df_peso_selected,
        0.0,
        value_column,
    )
    if general_days.empty or selected_days.empty:
        return pd.DataFrame(columns=columns)

    salary_data = monthly_costs[["Mes", source_column]].copy()
    salary_data["Mes"] = salary_data["Mes"].astype("string").str.strip()
    salary_data[source_column] = (
        pd.to_numeric(salary_data[source_column], errors="coerce")
        .fillna(0.0)
        .clip(lower=0)
    )
    salary_by_month = salary_data.groupby("Mes")[source_column].sum().to_dict()
    # Cada combinação de placa e data representa um dia trabalhado. Assim, o
    # salário mensal é dividido por toda a atividade da frota, não apenas pela
    # quantidade de datas existentes no calendário.
    general_days_by_month = general_days.groupby("Mes").size().to_dict()
    daily_rate_by_month = {
        str(month): float(salary_by_month.get(str(month), 0.0)) / int(day_count)
        for month, day_count in general_days_by_month.items()
        if day_count
    }

    selected_days["_DailyRate"] = (
        selected_days["Mes"]
        .astype("string")
        .map(daily_rate_by_month)
        .fillna(0.0)
        .astype("float64")
        .clip(lower=0)
    )
    selected_days[value_column] = selected_days["_DailyRate"]
    return selected_days[columns].copy()


def _ranking_freighter_daily_rates(registry: pd.DataFrame | None = None) -> dict[str, float]:
    if registry is None:
        try:
            registry = load_placas()
        except Exception:
            registry = _empty(_PLACAS_COLUMNS)
    required = {"PLACA", "Categoria", "Diaria"}
    if registry.empty or not required.issubset(registry.columns):
        return {}

    work = registry[["PLACA", "Categoria", "Diaria"]].copy()
    work["Categoria"] = work["Categoria"].apply(_normalize_category_value)
    work["Diaria"] = pd.to_numeric(work["Diaria"], errors="coerce").fillna(0.0).clip(lower=0)
    work = work[(work["Categoria"] == "Freteiro") & (work["Diaria"] > 0)].dropna(subset=["PLACA"])
    return {
        str(placa).strip(): float(diaria)
        for placa, diaria in work[["PLACA", "Diaria"]].itertuples(index=False)
        if str(placa).strip()
    }


def _ranking_freighter_daily_costs(
    df_peso: pd.DataFrame,
    daily_rates: dict[str, float] | None = None,
) -> pd.DataFrame:
    columns = ["Data", "Mes", "PLACA", "Diaria", "Custo"]
    if df_peso.empty or not {"Data", "PLACA"}.issubset(df_peso.columns):
        return pd.DataFrame(columns=columns)

    rates = daily_rates if daily_rates is not None else _ranking_freighter_daily_rates()
    if not rates:
        return pd.DataFrame(columns=columns)

    work = df_peso[["Data", "PLACA"]].copy()
    work["Data"] = pd.to_datetime(work["Data"], errors="coerce").dt.normalize()
    work["PLACA"] = work["PLACA"].astype("string").str.strip()
    work = work.dropna(subset=["Data", "PLACA"])
    work = work[(work["PLACA"] != "") & work["PLACA"].isin(rates)].drop_duplicates(["Data", "PLACA"])
    if work.empty:
        return pd.DataFrame(columns=columns)

    work["Mes"] = work["Data"].dt.to_period("M").astype("string")
    work["Diaria"] = work["PLACA"].map(rates).fillna(0.0)
    work["Custo"] = work["Diaria"]
    return work[columns].sort_values(["Data", "PLACA"]).reset_index(drop=True)


def _ranking_route_cost_gain_analysis(
    df_peso: pd.DataFrame,
    df_comb: pd.DataFrame,
    df_manu: pd.DataFrame,
    df_ped: pd.DataFrame,
    df_peso_transport_general: pd.DataFrame,
    df_salarios: pd.DataFrame,
    df_hoteis: pd.DataFrame,
    daily_rates: dict[str, float],
) -> dict:
    """Calcula custo/ganho por rota no período e em cada mês, sem novas consultas."""
    empty_result = {"rotas": [], "mensal": []}
    if df_peso.empty or not {"Data", "Mes", "PLACA"}.issubset(df_peso.columns):
        return empty_result

    peso = df_peso.copy()
    peso["_RotaAnalise"] = _ranking_route_labels(peso)
    peso["Mes"] = peso["Mes"].astype("string").str.strip()
    route_names = sorted(
        {str(route).strip() for route in peso["_RotaAnalise"].dropna().tolist() if str(route).strip()},
        key=str.casefold,
    )
    if not route_names:
        return empty_result

    def _period(df: pd.DataFrame, month: str | None) -> pd.DataFrame:
        if month is None or df.empty or "Mes" not in df.columns:
            return df
        return df.loc[df["Mes"].astype("string").str.strip().eq(str(month))].copy()

    def _sum_value(df: pd.DataFrame, column: str) -> float:
        if df.empty or column not in df.columns:
            return 0.0
        return float(pd.to_numeric(df[column], errors="coerce").fillna(0.0).sum())

    def _scope_rows(month: str | None) -> list[dict]:
        peso_scope = _period(peso, month)
        comb_scope = _period(df_comb, month)
        manu_scope = _period(df_manu, month)
        ped_scope = _period(df_ped, month)
        transport_scope = _period(df_peso_transport_general, month)
        salary_scope = _period(df_salarios, month)
        hotel_scope = _period(df_hoteis, month)
        if peso_scope.empty:
            return []

        day_columns = ["Data", "Mes", "PLACA", "_RotaAnalise"]
        route_days = peso_scope[day_columns].copy()
        route_days["Data"] = pd.to_datetime(route_days["Data"], errors="coerce").dt.normalize()
        route_days["PLACA"] = route_days["PLACA"].astype("string").str.strip()
        route_days = route_days.dropna(subset=["Data", "PLACA", "_RotaAnalise"])
        route_days = route_days[route_days["PLACA"] != ""].drop_duplicates(
            ["Data", "PLACA", "_RotaAnalise"]
        )
        if route_days.empty:
            return []
        route_day_pairs = route_days[["_RotaAnalise", "Data", "PLACA"]].drop_duplicates()

        def _route_plate_costs(df: pd.DataFrame, column: str) -> dict[str, float]:
            if df.empty or not {"Data", "PLACA", column}.issubset(df.columns):
                return {}
            costs = df[["Data", "PLACA", column]].copy()
            costs["Data"] = pd.to_datetime(costs["Data"], errors="coerce").dt.normalize()
            costs["PLACA"] = costs["PLACA"].astype("string").str.strip()
            costs[column] = pd.to_numeric(costs[column], errors="coerce").fillna(0.0)
            costs = costs.dropna(subset=["Data", "PLACA"])
            costs = costs[costs["PLACA"] != ""].groupby(["Data", "PLACA"], as_index=False)[column].sum()
            merged = route_day_pairs.merge(costs, on=["Data", "PLACA"], how="left")
            merged[column] = merged[column].fillna(0.0)
            return {
                str(route): float(value or 0.0)
                for route, value in merged.groupby("_RotaAnalise")[column].sum().items()
            }

        def _route_named_costs(df: pd.DataFrame, column: str) -> dict[str, float]:
            if df.empty or not {"Rota", column}.issubset(df.columns):
                return {}
            costs = df[["Rota", column]].copy()
            costs["_RouteKey"] = costs["Rota"].astype("string").fillna("").str.strip().str.casefold()
            costs[column] = pd.to_numeric(costs[column], errors="coerce").fillna(0.0)
            costs = costs[costs["_RouteKey"] != ""].groupby("_RouteKey", as_index=False)[column].sum()
            route_keys = route_days[["_RotaAnalise"]].drop_duplicates()
            route_keys["_RouteKey"] = route_keys["_RotaAnalise"].astype("string").str.strip().str.casefold()
            merged = route_keys.merge(costs, on="_RouteKey", how="left")
            merged[column] = merged[column].fillna(0.0)
            return {
                str(route): float(value or 0.0)
                for route, value in merged.groupby("_RotaAnalise")[column].sum().items()
            }

        fuel_by_route = _route_named_costs(comb_scope, "Custo")
        toll_by_route = _route_plate_costs(ped_scope, "Custo")

        general_days_by_plate = _ranking_active_days_by_plate(peso_scope)
        maintenance_by_plate = _ranking_sum_by_plate(manu_scope, "Custo")
        maintenance_daily_by_plate = {
            plate: maintenance_by_plate.get(plate, 0.0) / active_days
            for plate, active_days in general_days_by_plate.items()
            if active_days
        }
        route_days["_Maintenance"] = (
            route_days["PLACA"].map(maintenance_daily_by_plate).fillna(0.0).clip(lower=0)
        )
        maintenance_by_route = {
            str(route): float(value or 0.0)
            for route, value in route_days.groupby("_RotaAnalise")["_Maintenance"].sum().items()
        }
        route_days["_Freighter"] = route_days["PLACA"].map(daily_rates).fillna(0.0).clip(lower=0)
        freighter_by_route = {
            str(route): float(value or 0.0)
            for route, value in route_days.groupby("_RotaAnalise")["_Freighter"].sum().items()
        }

        salary_by_route: dict[str, float] = {}
        transport_selected = _ranking_filter_categories(peso_scope, ["Transporte"])
        if not transport_selected.empty:
            selected_transport_days = transport_selected[day_columns].copy()
            selected_transport_days["Data"] = pd.to_datetime(
                selected_transport_days["Data"], errors="coerce"
            ).dt.normalize()
            selected_transport_days["PLACA"] = selected_transport_days["PLACA"].astype("string").str.strip()
            selected_transport_days = selected_transport_days.dropna(
                subset=["Data", "PLACA", "_RotaAnalise"]
            ).drop_duplicates(["Data", "PLACA", "_RotaAnalise"])
            general_transport_days = _ranking_daily_average_costs(transport_scope, 0.0, "Custo")
            salary_data = (
                salary_scope[["Mes", "Valor"]].copy()
                if not salary_scope.empty and {"Mes", "Valor"}.issubset(salary_scope.columns)
                else pd.DataFrame(columns=["Mes", "Valor"])
            )
            if not general_transport_days.empty and not salary_data.empty:
                salary_data["Mes"] = salary_data["Mes"].astype("string").str.strip()
                salary_data["Valor"] = pd.to_numeric(
                    salary_data["Valor"], errors="coerce"
                ).fillna(0.0).clip(lower=0)
                salary_by_month = salary_data.groupby("Mes")["Valor"].sum().to_dict()
                general_days_by_month = general_transport_days.groupby("Mes").size().to_dict()
                salary_daily_by_month = {
                    str(key): float(salary_by_month.get(str(key), 0.0)) / int(day_count)
                    for key, day_count in general_days_by_month.items()
                    if day_count
                }
                selected_transport_days["_Salary"] = (
                    selected_transport_days["Mes"]
                    .astype("string")
                    .map(salary_daily_by_month)
                    .fillna(0.0)
                    .clip(lower=0)
                )
                salary_by_route = {
                    str(route): float(value or 0.0)
                    for route, value in selected_transport_days.groupby("_RotaAnalise")["_Salary"].sum().items()
                }

        hotel_days = _sum_value(hotel_scope, "Dias") if "Dias" in hotel_scope.columns else 0.0
        hotel_daily = _sum_value(hotel_scope, "Valor") / hotel_days if hotel_days else 0.0
        route_day_counts = route_days.groupby("_RotaAnalise").size().to_dict()
        gain_data = peso_scope[["_RotaAnalise", "Valor"]].copy()
        gain_data["Valor"] = pd.to_numeric(gain_data["Valor"], errors="coerce").fillna(0.0)
        gain_by_route = gain_data.groupby("_RotaAnalise")["Valor"].sum().to_dict()

        rows: list[dict] = []
        for route_name in sorted(route_day_counts, key=lambda value: str(value).casefold()):
            route_key = str(route_name)
            gain_total = float(gain_by_route.get(route_name, 0.0) or 0.0)
            cost_total = (
                float(fuel_by_route.get(route_key, 0.0))
                + float(toll_by_route.get(route_key, 0.0))
                + float(maintenance_by_route.get(route_key, 0.0))
                + float(freighter_by_route.get(route_key, 0.0))
                + float(salary_by_route.get(route_key, 0.0))
                + max(hotel_daily, 0.0) * int(route_day_counts.get(route_name, 0) or 0)
            )
            no_gain = gain_total <= 0 and cost_total > 0
            percentage = (cost_total / gain_total * 100) if gain_total > 0 else None
            level = (
                "vermelho"
                if no_gain or (percentage is not None and percentage >= 100)
                else "amarelo"
                if percentage is not None and percentage >= 80
                else "normal"
            )
            rows.append(
                {
                    "rota": route_key,
                    "mes": month,
                    "custo": round(cost_total, 2),
                    "ganho": round(gain_total, 2),
                    "percentual": round(percentage, 2) if percentage is not None else None,
                    "sem_ganho": no_gain,
                    "nivel": level,
                }
            )
        return rows

    route_rows = _scope_rows(None)
    route_rows.sort(
        key=lambda row: (
            bool(row.get("sem_ganho")),
            float(row.get("percentual") or 0.0),
            float(row.get("custo") or 0.0),
        ),
        reverse=True,
    )
    months = sorted({str(month).strip() for month in peso["Mes"].dropna().tolist() if str(month).strip()})
    monthly_rows = [row for month in months for row in _scope_rows(month)]
    monthly_rows.sort(
        key=lambda row: (
            2 if row.get("nivel") == "vermelho" else 1 if row.get("nivel") == "amarelo" else 0,
            bool(row.get("sem_ganho")),
            float(row.get("percentual") or 0.0),
            str(row.get("mes") or ""),
            str(row.get("rota") or ""),
        ),
        reverse=True,
    )
    return {"rotas": route_rows, "mensal": monthly_rows}


def _ranking_monthly_sum(frames: list[tuple[pd.DataFrame, str]], value_name: str) -> dict:
    monthly: dict[str, float] = defaultdict(float)
    for df, value_col in frames:
        if df.empty or "Mes" not in df.columns or value_col not in df.columns:
            continue
        data = df[["Mes", value_col]].dropna(subset=["Mes"]).copy()
        data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
        data = data.dropna(subset=[value_col])
        for mes_val, value in data.groupby("Mes")[value_col].sum().items():
            key = str(mes_val).strip()
            if key:
                monthly[key] += float(value or 0.0)
    meses = sorted(monthly)
    return {"Mes": meses, value_name: [round(monthly[mes], 3) for mes in meses]}


def _ranking_monthly_km(df_km: pd.DataFrame, _df_comb: pd.DataFrame) -> dict:
    def _monthly_map(df: pd.DataFrame, value_col: str) -> dict[str, float]:
        if df.empty or "Mes" not in df.columns or value_col not in df.columns:
            return {}
        values = pd.to_numeric(df[value_col], errors="coerce")
        data = df.loc[values.notna(), ["Mes"]].copy()
        data[value_col] = values.loc[values.notna()].astype("float64")
        data["Mes"] = data["Mes"].astype("string").str.strip()
        data = data.dropna(subset=["Mes"])
        if data.empty:
            return {}
        grouped = data.groupby("Mes")[value_col].sum()
        return {str(mes): float(value) for mes, value in grouped.items() if pd.notna(value)}

    km_override = _monthly_map(df_km, "Km Rodados")
    meses = sorted(km_override)
    return {"Mes": meses, "Km Rodados": [round(km_override.get(mes, 0.0), 3) for mes in meses]}


def _ranking_weight_dominance_by_route(df: pd.DataFrame, placas: list[str] | None = None) -> dict:
    if df.empty or not {"PLACA", "Peso"}.issubset(df.columns):
        return {"labels": [], "values": [], "route_counts": [], "rotas": []}

    columns = ["PLACA", "Peso"]
    if "Rota" in df.columns:
        columns.insert(0, "Rota")
    work = df[columns].copy()
    work["Rota"] = _ranking_route_labels(work).str.upper()
    work["PLACA"] = work["PLACA"].astype("string").str.strip()
    work["Peso"] = pd.to_numeric(work["Peso"], errors="coerce").fillna(0.0)
    work = work[
        (work["Rota"].notna())
        & (work["Rota"] != "")
        & (work["PLACA"].notna())
        & (work["PLACA"] != "")
        & (work["Peso"] > 0)
    ]
    if work.empty:
        return {"labels": [], "values": [], "route_counts": [], "rotas": []}

    grouped = work.groupby(["Rota", "PLACA"], as_index=False)["Peso"].sum()
    route_totals = grouped.groupby("Rota", as_index=False)["Peso"].sum().rename(columns={"Peso": "PesoRota"})
    grouped = grouped.merge(route_totals, on="Rota", how="left")
    grouped["Participacao"] = grouped.apply(
        lambda row: float(row["Peso"] / row["PesoRota"] * 100) if row["PesoRota"] else 0.0,
        axis=1,
    )
    placas = [str(placa).strip() for placa in (placas or []) if str(placa).strip()]
    if placas:
        selected = grouped[grouped["PLACA"].isin(placas)].copy()
    else:
        selected = grouped.copy()

    if selected.empty:
        return {"labels": [], "values": [], "route_counts": [], "rotas": []}

    by_plate = selected.groupby("PLACA", as_index=False).agg(Peso=("Peso", "sum"), Rotas=("Rota", "count"))
    by_plate = by_plate.sort_values(["Peso", "Rotas", "PLACA"], ascending=[False, False, True])
    routes = selected.sort_values(["Rota", "Peso", "PLACA"], ascending=[True, False, True])
    return {
        "labels": [str(item) for item in by_plate["PLACA"].tolist()],
        "values": [round(float(item), 3) for item in by_plate["Peso"].tolist()],
        "route_counts": [int(item) for item in by_plate["Rotas"].tolist()],
        "rotas": [
            {
                "rota": str(row["Rota"]),
                "placa": str(row["PLACA"]),
                "peso": round(float(row["Peso"]), 3),
                "peso_rota": round(float(row["PesoRota"]), 3),
                "participacao": round(float(row["Participacao"]), 2),
            }
            for _, row in routes.iterrows()
        ],
    }


def _ranking_category_map(*frames: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for df in frames:
        if df.empty or "PLACA" not in df.columns or "Categoria" not in df.columns:
            continue
        for placa, categoria in df[["PLACA", "Categoria"]].dropna().itertuples(index=False):
            placa_key = str(placa).strip()
            if placa_key:
                mapping[placa_key] = str(categoria).strip() or "Transporte"
    try:
        registry = load_placas()
    except Exception:
        registry = _empty(_PLACAS_COLUMNS)
    if not registry.empty and "PLACA" in registry.columns and "Categoria" in registry.columns:
        for placa, categoria in registry[["PLACA", "Categoria"]].dropna().itertuples(index=False):
            placa_key = str(placa).strip()
            if placa_key:
                mapping[placa_key] = str(categoria).strip() or "Transporte"
    return mapping


def data_frota(params: dict | None = None) -> dict:
    params = params or {}
    ano = _parse_int(_param(params, "ano"))
    meses = _parse_mes_list(params.get("mes"))
    categorias_filtro = _ranking_parse_category_list(params.get("categoria"))
    incluir_salario = (
        _truthy_param(params.get("incluir_salario"))
        if "incluir_salario" in params
        else True
    )
    include_transport_salary = incluir_salario and (
        not categorias_filtro
        or any(_normalize_category_value(category) == "Transporte" for category in categorias_filtro)
    )
    incluir_hoteis = _truthy_param(params.get("incluir_hoteis"))
    rotas = _ranking_parse_route_list(params.get("rota"))
    placas = _ranking_parse_plate_list(params.get("placa"))
    ordenar_por = str(_param(params, "ordenar_por") or "total").strip().lower()

    df_comb = _ranking_filter_valid_plates(_apply_plate_categories(load_combustivel()))
    df_manu = _ranking_filter_valid_plates(_apply_plate_categories(load_manutencao()))
    df_ped = _ranking_filter_valid_plates(_apply_plate_categories(load_pedagio()))
    df_km = _ranking_filter_valid_plates(_apply_plate_categories(load_combustivel_km()))
    df_peso_all = _apply_plate_categories(load_peso())
    df_peso = _ranking_filter_valid_plates(df_peso_all)
    df_rodagem_rota = _ranking_filter_valid_plates(_apply_plate_categories(load_rodagem_rota()))
    df_hoteis = _apply_plate_categories(load_hoteis())
    df_salarios_transporte = (
        load_salarios_transporte()
        if include_transport_salary
        else _empty(_SALARIOS_TRANSPORTE_COLUMNS)
    )
    df_peso_transport_base = _ranking_filter_categories(df_peso, ["Transporte"])

    source_frames = [df_comb, df_manu, df_ped, df_peso]
    categoria_frames = [_ranking_filter_categories(df, categorias_filtro) for df in source_frames]
    df_comb_base, df_manu_base, df_ped_base, df_peso_base = categoria_frames
    df_km_base = _ranking_filter_categories(df_km, categorias_filtro)
    df_rodagem_rota_base = _ranking_filter_categories(df_rodagem_rota, categorias_filtro)
    df_hoteis_base = _ranking_filter_categories(df_hoteis, categorias_filtro)

    anos_disponiveis: set[int] = set()
    year_frames = [df_comb_base, df_manu_base, df_ped_base, df_km_base, df_peso_base, df_peso_all, df_rodagem_rota_base]
    if incluir_hoteis:
        year_frames.append(df_hoteis_base)
    if include_transport_salary:
        year_frames.append(df_salarios_transporte)
    for df_src in year_frames:
        anos_disponiveis.update(_unique_years(df_src))
        anos_disponiveis.update(df_src.attrs.get("anos_sheets", []))

    month_frames = [df_comb_base, df_manu_base, df_ped_base, df_km_base, df_peso_base, df_peso_all, df_rodagem_rota_base]
    if incluir_hoteis:
        month_frames.append(df_hoteis_base)
    if include_transport_salary:
        month_frames.append(df_salarios_transporte)
    if ano is not None:
        month_frames = [_filter_by_period(df, ano=ano) for df in month_frames]
    month_source = [df[["Mes"]] for df in month_frames if not df.empty and "Mes" in df.columns]
    meses_disponiveis = _unique_sorted(pd.concat(month_source, ignore_index=True), "Mes") if month_source else []

    def _apply_period(df: pd.DataFrame) -> pd.DataFrame:
        if ano is not None:
            df = _filter_by_period(df, ano=ano)
        if meses and "Mes" in df.columns:
            df = df[df["Mes"].isin(meses)]
        return df.copy()

    df_comb = _apply_period(df_comb_base)
    df_comb_metrics = df_comb.copy()
    df_manu = _apply_period(df_manu_base)
    df_ped = _apply_period(df_ped_base)
    df_km = _apply_period(df_km_base)
    df_peso = _apply_period(df_peso_base)
    df_peso_total = _apply_period(df_peso_all)
    df_rodagem_rota = _apply_period(df_rodagem_rota_base)
    df_peso_transport_general = _apply_period(df_peso_transport_base)
    df_hoteis_period = _apply_period(df_hoteis_base) if incluir_hoteis else df_hoteis_base.iloc[0:0].copy()
    df_salarios_period = (
        _apply_period(df_salarios_transporte)
        if include_transport_salary
        else _empty(_SALARIOS_TRANSPORTE_COLUMNS)
    )

    df_manu_general = df_manu.copy()
    df_peso_general = df_peso.copy()
    df_peso_route_eligible = _ranking_eligible_route_weight_rows(
        df_peso_general,
        df_rodagem_rota,
    )
    general_days_by_plate = _ranking_active_days_by_plate(df_peso_general)
    general_maintenance_by_plate = _ranking_sum_by_plate(df_manu_general, "Custo")
    maintenance_daily_by_plate = {
        plate: general_maintenance_by_plate.get(plate, 0.0) / active_days
        for plate, active_days in general_days_by_plate.items()
        if active_days
    }
    general_hotel_total = (
        float(pd.to_numeric(df_hoteis_period.get("Valor"), errors="coerce").fillna(0).sum())
        if incluir_hoteis and "Valor" in df_hoteis_period.columns
        else 0.0
    )
    general_hotel_days = (
        float(pd.to_numeric(df_hoteis_period.get("Dias"), errors="coerce").fillna(0).clip(lower=0).sum())
        if incluir_hoteis and "Dias" in df_hoteis_period.columns
        else 0.0
    )
    hotel_daily_average = (
        general_hotel_total / general_hotel_days
        if general_hotel_days
        else 0.0
    )
    daily_rates = _ranking_freighter_daily_rates()
    route_fuel_costs_all, _route_fuel_warnings_all = _ranking_route_fuel_costs(
        df_rodagem_rota,
        df_comb,
        df_km,
    )
    route_cost_gain_analysis = _ranking_route_cost_gain_analysis(
        _ranking_filter_plates(df_peso_route_eligible, placas),
        _ranking_filter_plates(route_fuel_costs_all, placas),
        _ranking_filter_plates(df_manu_general, placas),
        _ranking_filter_plates(df_ped, placas),
        df_peso_transport_general,
        df_salarios_period,
        df_hoteis_period,
        daily_rates,
    )

    rotas_disponiveis = sorted(
        _ranking_route_labels(df_peso_route_eligible).dropna().unique().tolist()
    )
    route_fuel_warnings: list[str] = []
    if rotas:
        df_peso = _ranking_filter_routes(df_peso_route_eligible, rotas)
        df_peso_total = df_peso.copy()
        route_plates = (
            sorted(
                {
                    str(value).strip()
                    for value in df_peso.get("PLACA", pd.Series(dtype="string")).dropna().tolist()
                    if _ranking_valid_plate(value)
                }
            )
            if not df_peso.empty
            else []
        )
        if route_plates:
            selected_route_km = _ranking_filter_routes(df_rodagem_rota, rotas)
            selected_route_km = _ranking_filter_plates(selected_route_km, route_plates)
            selected_route_km = _ranking_filter_plates(selected_route_km, placas)
            df_comb, route_fuel_warnings = _ranking_route_fuel_costs(
                selected_route_km,
                df_comb,
                df_km,
            )
            needs_mileage = (
                "Categoria" in df_peso.columns
                and not _normalize_categoria(df_peso["Categoria"]).eq("freteiro").all()
            )
            if needs_mileage and selected_route_km.empty:
                route_fuel_warnings.append(
                    "Nenhuma rodagem em KM foi cadastrada para as rotas, placas e meses selecionados."
                )
            df_ped = _ranking_filter_route_day_rows(df_ped, df_peso)
            df_comb_metrics = _ranking_filter_plates(df_comb_metrics, route_plates)
            df_manu = _ranking_filter_plates(df_manu, route_plates)
            df_km = _ranking_filter_plates(df_km, route_plates)
        else:
            df_comb = df_comb.iloc[0:0].copy()
            df_ped = df_ped.iloc[0:0].copy()
            df_comb_metrics = df_comb_metrics.iloc[0:0].copy()
            df_manu = df_manu.iloc[0:0].copy()
            df_km = df_km.iloc[0:0].copy()

    plate_source = [
        df[["PLACA"]]
        for df in (df_comb, df_comb_metrics, df_manu, df_ped, df_km, df_peso)
        if not df.empty and "PLACA" in df.columns
    ]
    placas_disponiveis = _unique_sorted(pd.concat(plate_source, ignore_index=True), "PLACA") if plate_source else []
    df_peso_dominancia = _ranking_filter_valid_plates(df_peso_total)

    df_comb = _ranking_filter_plates(df_comb, placas)
    df_comb_metrics = _ranking_filter_plates(df_comb_metrics, placas)
    df_manu = _ranking_filter_plates(df_manu, placas)
    df_ped = _ranking_filter_plates(df_ped, placas)
    df_km = _ranking_filter_plates(df_km, placas)
    df_peso = _ranking_filter_plates(df_peso, placas)
    df_peso_total = _ranking_filter_plates(df_peso_total, placas)
    df_peso_transport_selected = _ranking_filter_categories(df_peso, ["Transporte"])
    dominancia_peso = _ranking_weight_dominance_by_route(df_peso_dominancia, placas)
    route_days_map = _ranking_active_days_by_plate(df_peso) if rotas else {}
    service_days_map = _ranking_active_days_by_plate(df_peso)

    categorias = set()
    for df_src in source_frames:
        categorias.update(_unique_sorted(df_src, "Categoria"))

    category_map = _ranking_category_map(df_comb_base, df_manu_base, df_ped_base, df_peso_base)
    total_comb = _ranking_sum_by_plate(df_comb, "Custo")
    if rotas:
        df_manu_costs = _ranking_daily_plate_costs(
            df_peso,
            maintenance_daily_by_plate,
            "Custo",
        )
    else:
        df_manu_costs = df_manu
    total_manu = _ranking_sum_by_plate(df_manu_costs, "Custo")
    total_ped = _ranking_sum_by_plate(df_ped, "Custo")
    peso_map = _ranking_sum_by_plate(df_peso, "Peso")
    valor_peso_map = _ranking_sum_by_plate(df_peso, "Valor")
    df_diarias = _ranking_freighter_daily_costs(df_peso, daily_rates)
    gasto_diarias_map = _ranking_sum_by_plate(df_diarias, "Custo")
    dias_trabalhados_map = _ranking_count_by_plate(df_diarias)
    df_salario_costs = _ranking_monthly_shared_costs(
        df_peso_transport_selected,
        df_peso_transport_general,
        df_salarios_period,
    )
    salario_transporte_map = _ranking_sum_by_plate(df_salario_costs, "Custo")
    total_salario_cadastrado = (
        float(pd.to_numeric(df_salarios_period.get("Valor"), errors="coerce").fillna(0).sum())
        if "Valor" in df_salarios_period.columns
        else 0.0
    )
    total_salario_rateado = (
        float(pd.to_numeric(df_salario_costs.get("Custo"), errors="coerce").fillna(0).sum())
        if "Custo" in df_salario_costs.columns
        else 0.0
    )
    salario_nao_rateado = (
        max(total_salario_cadastrado - total_salario_rateado, 0.0)
        if include_transport_salary and not rotas and not placas
        else 0.0
    )
    df_salario_nao_rateado = pd.DataFrame(columns=["Mes", "Custo"])
    if salario_nao_rateado > 0 and not df_salarios_period.empty:
        allocated_by_month = (
            df_salario_costs.groupby("Mes")["Custo"].sum().to_dict()
            if not df_salario_costs.empty
            else {}
        )
        salary_by_month = (
            df_salarios_period.groupby("Mes")["Valor"].sum().to_dict()
        )
        remainder_rows = [
            {
                "Mes": str(month),
                "Custo": max(float(value or 0.0) - float(allocated_by_month.get(month, 0.0)), 0.0),
            }
            for month, value in salary_by_month.items()
            if float(value or 0.0) - float(allocated_by_month.get(month, 0.0)) > 0
        ]
        df_salario_nao_rateado = pd.DataFrame(remainder_rows, columns=["Mes", "Custo"])
    df_hoteis_costs = (
        _ranking_daily_average_costs(df_peso, hotel_daily_average, "Valor")
        if incluir_hoteis and rotas
        else df_hoteis_period
    )
    total_hoteis = (
        float(pd.to_numeric(df_hoteis_costs.get("Valor"), errors="coerce").fillna(0).sum())
        if incluir_hoteis and "Valor" in df_hoteis_costs.columns
        else 0.0
    )
    total_dias_hoteis = (
        float(len(df_hoteis_costs))
        if incluir_hoteis and rotas
        else general_hotel_days
    )
    hoteis_diaria = hotel_daily_average
    mensal_total_frames = [
        (df_comb, "Custo"),
        (df_manu_costs, "Custo"),
        (df_ped, "Custo"),
        (df_diarias, "Custo"),
        (df_salario_costs, "Custo"),
        (df_salario_nao_rateado, "Custo"),
    ]
    if incluir_hoteis:
        mensal_total_frames.append((df_hoteis_costs, "Valor"))
    mensal_total = _ranking_monthly_sum(mensal_total_frames, "Valor")
    mensal_peso = _ranking_monthly_sum([(df_peso_total, "Peso")], "Peso")
    mensal_km = _ranking_monthly_km(df_km, df_comb_metrics)
    mensal_litros = _ranking_monthly_sum([(df_comb_metrics, "Litros")], "Litros")
    litros_map = _ranking_sum_by_plate(df_comb_metrics, "Litros")
    km_override_map = _ranking_sum_by_plate(df_km, "Km Rodados")
    abastecimentos_map = _ranking_count_by_plate(df_comb_metrics)
    servicos_map = _ranking_count_by_plate(df_manu)
    pedagio_count_map = _ranking_count_by_plate(df_ped)

    placa_set = (
        set(total_comb)
        | set(total_manu)
        | set(total_ped)
        | set(litros_map)
        | set(km_override_map)
        | set(peso_map)
        | set(gasto_diarias_map)
        | set(salario_transporte_map)
    )
    ranking = []
    for placa in sorted(placa_set):
        categoria = category_map.get(placa, "Transporte")
        combustivel_total = total_comb.get(placa, 0.0)
        manutencao_total = total_manu.get(placa, 0.0)
        pedagio_total = total_ped.get(placa, 0.0)
        dias_na_rota = route_days_map.get(placa, 0)
        manutencao_diaria = (manutencao_total / dias_na_rota) if dias_na_rota else 0.0
        peso_total = peso_map.get(placa, 0.0)
        valor_peso_total = valor_peso_map.get(placa, 0.0)
        diaria = daily_rates.get(placa, 0.0) if _normalize_category_value(categoria) == "Freteiro" else 0.0
        dias_trabalhados = dias_trabalhados_map.get(placa, 0)
        dias_servico = service_days_map.get(placa, 0)
        gasto_diarias = gasto_diarias_map.get(placa, 0.0)
        salario_transporte = salario_transporte_map.get(placa, 0.0)
        total = combustivel_total + manutencao_total + pedagio_total + gasto_diarias + salario_transporte
        km_total = km_override_map.get(placa, 0.0)
        litros_total = litros_map.get(placa, 0.0)
        lancamentos = abastecimentos_map.get(placa, 0) + servicos_map.get(placa, 0) + pedagio_count_map.get(placa, 0)
        ranking.append(
            {
                "placa": placa,
                "categoria": categoria,
                "total": round(total, 2),
                "combustivel": round(combustivel_total, 2),
                "manutencao": round(manutencao_total, 2),
                "manutencao_diaria": round(manutencao_diaria, 2),
                "dias_na_rota": dias_na_rota,
                "pedagio": round(pedagio_total, 2),
                "diaria": round(diaria, 2),
                "dias_trabalhados": dias_trabalhados,
                "dias_servico": dias_servico,
                "gasto_diarias": round(gasto_diarias, 2),
                "salario_transporte": round(salario_transporte, 2),
                "peso_total": round(peso_total, 3),
                "valor_peso": round(valor_peso_total, 2),
                "km_total": round(km_total, 2),
                "litros_total": round(litros_total, 2),
                "custo_por_km": round((total / km_total) if km_total else 0.0, 4),
                "combustivel_por_km": round((combustivel_total / km_total) if km_total else 0.0, 4),
                "km_por_litro": round((km_total / litros_total) if litros_total else 0.0, 3),
                "custo_por_litro": round((combustivel_total / litros_total) if litros_total else 0.0, 4),
                "abastecimentos": abastecimentos_map.get(placa, 0),
                "servicos": servicos_map.get(placa, 0),
                "despesas_pedagio": pedagio_count_map.get(placa, 0),
                "lancamentos": lancamentos,
            }
        )

    sort_keys = {
        "total": "total",
        "combustivel": "combustivel",
        "manutencao": "manutencao",
        "pedagio": "pedagio",
        "diarias": "gasto_diarias",
        "peso": "peso_total",
        "valor_entregas": "valor_peso",
        "dias_servico": "dias_servico",
    }
    sort_selection = ordenar_por if ordenar_por in sort_keys else "combustivel"
    sort_key = sort_keys[sort_selection]
    ranking.sort(key=lambda row: (row.get(sort_key, 0.0), row.get("total", 0.0), row.get("placa", "")), reverse=True)
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index

    total_km = sum(row["km_total"] for row in ranking)
    total_litros = sum(row["litros_total"] for row in ranking)
    total_gasto_placas = sum(row["total"] for row in ranking)
    total_salario_transporte = sum(row["salario_transporte"] for row in ranking) + salario_nao_rateado
    total_salario_transporte_dias = (
        int(df_salario_costs.drop_duplicates(["Data", "PLACA"]).shape[0])
        if not df_salario_costs.empty and {"Data", "PLACA"}.issubset(df_salario_costs.columns)
        else 0
    )
    total_gasto = total_gasto_placas + total_hoteis + salario_nao_rateado
    total_placas = len(ranking)
    total_dias_na_rota = sum(row["dias_na_rota"] for row in ranking)
    total_manutencao = sum(row["manutencao"] for row in ranking)
    total_peso_filtrado = sum(row["peso_total"] for row in ranking)
    total_peso_geral = (
        float(pd.to_numeric(df_peso_total.get("Peso"), errors="coerce").fillna(0).sum())
        if "Peso" in df_peso_total.columns
        else 0.0
    )
    total_entregas = int(len(df_peso_total))
    total_dias_servico = sum(row["dias_servico"] for row in ranking)
    peso_sem_rota = (
        float(
            pd.to_numeric(
                df_peso_total.loc[_ranking_route_labels(df_peso_total) == _RANKING_NO_ROUTE_LABEL, "Peso"],
                errors="coerce",
            )
            .fillna(0)
            .sum()
        )
        if "Peso" in df_peso_total.columns
        else 0.0
    )
    periodos_media = set(meses) if meses else {str(mes) for mes in meses_disponiveis if str(mes).strip()}
    total_meses = len(periodos_media)

    return {
        "anos": sorted(anos_disponiveis),
        "meses": meses_disponiveis,
        "categorias": sorted(categorias),
        "rotas": rotas_disponiveis,
        "placas": placas_disponiveis,
        "ordenar_por": sort_selection,
        "ranking": ranking,
        "custo_ganho_rotas": route_cost_gain_analysis,
        "avisos_rodagem_rota": route_fuel_warnings,
        "dominancia_peso": dominancia_peso,
        "mensal_total": mensal_total,
        "peso_mensal": mensal_peso,
        "km_mensal": mensal_km,
        "litros_mensal": mensal_litros,
        "totais": {
            "placas": total_placas,
            "total": round(total_gasto, 2),
            "media_mensal": round((total_gasto / total_meses) if total_meses else 0.0, 2),
            "combustivel": round(sum(row["combustivel"] for row in ranking), 2),
            "manutencao": round(total_manutencao, 2),
            "manutencao_diaria": round((total_manutencao / total_dias_na_rota) if total_dias_na_rota else 0.0, 2),
            "dias_na_rota": total_dias_na_rota,
            "pedagio": round(sum(row["pedagio"] for row in ranking), 2),
            "gasto_diarias": round(sum(row["gasto_diarias"] for row in ranking), 2),
            "salario_transporte": round(total_salario_transporte, 2),
            "salario_transporte_dias": total_salario_transporte_dias,
            "dias_trabalhados": sum(row["dias_trabalhados"] for row in ranking),
            "hoteis": round(total_hoteis, 2),
            "hoteis_diaria": round(hoteis_diaria, 2),
            "dias_hoteis": round(total_dias_hoteis, 2),
            "inclui_hoteis": incluir_hoteis,
            "inclui_salario": include_transport_salary,
            "peso_total": round(total_peso_geral, 3),
            "peso_total_filtrado": round(total_peso_filtrado, 3),
            "peso_sem_rota": round(peso_sem_rota, 3),
            "entregas": total_entregas,
            "dias_servico": total_dias_servico,
            "valor_peso": round(sum(row["valor_peso"] for row in ranking), 2),
            "km_total": round(total_km, 2),
            "litros_total": round(total_litros, 2),
            "custo_por_km": round((total_gasto / total_km) if total_km else 0.0, 4),
            "km_por_litro": round((total_km / total_litros) if total_litros else 0.0, 3),
            "lancamentos": sum(row["lancamentos"] for row in ranking),
        },
    }


def _warm_data_caches(*, blocking: bool = False) -> None:
    loaders = (
        (load_combustivel, "combustivel"),
        (load_manutencao, "manutencao"),
        (load_pneus, "pneus"),
        (load_hoteis, "hoteis"),
        (load_pedagio, "pedagio/seguro/IPVA"),
        (load_peso, "peso"),
        (load_rodagem_rota, "rodagem por rota"),
        (load_salarios_transporte, "salarios do transporte"),
    )

    def _run() -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(loaders)) as pool:
            future_map = {pool.submit(loader): label for loader, label in loaders}
            for future, label in future_map.items():
                try:
                    future.result()
                except Exception as exc:  # pragma: no cover
                    print(f"Aviso: nao foi possivel pre-carregar {label} ({exc})")

    if blocking:
        _run()
    else:
        threading.Thread(target=_run, daemon=True).start()


if os.environ.get("JR_SKIP_WARM_CACHE", "").strip().lower() not in {"1", "true", "yes"}:
    _warm_data_caches(blocking=os.environ.get("WARM_CACHE_SYNC", "").strip().lower() in {"1", "true", "yes"})


def _safe_total(
    loader,
    aggregator,
    key: str,
    value_col: str | None = None,
    *,
    ano: int | None = None,
    mes: int | None = None,
    meses: list[int] | None = None,
) -> dict:
    try:
        df = loader()
    except Exception as exc:  # pragma: no cover
        return {"status": "erro", "motivo": str(exc), "valor": None, "categorias": None, "anos_sheets": []}

    periodos_disponiveis: list[str] = []
    anos_sheets = df.attrs.get("anos_sheets", [])
    if "Mes" in df.columns:
        period_series = pd.to_datetime(df["Mes"], errors="coerce").dt.to_period("M")
        periodos_disponiveis = sorted({str(periodo) for periodo in period_series.dropna().unique()})

    df = _filter_by_period(df, ano=ano, mes=mes, meses=meses or [])
    try:
        resumo = aggregator(df)
    except Exception as exc:  # pragma: no cover
        return {"status": "erro", "motivo": str(exc), "valor": None, "categorias": None, "anos_sheets": anos_sheets}

    valor = float(resumo.get(key, 0.0)) if resumo else 0.0
    categorias = None
    if value_col and value_col in df.columns and "Categoria" in df.columns:
        df_categoria = df.copy()
        df_categoria[value_col] = pd.to_numeric(df_categoria[value_col], errors="coerce")
        grupos = (
            df_categoria.groupby(
                df_categoria["Categoria"].astype("string").str.strip().str.title().replace({"": "Outros"}).fillna("Outros")
            )[value_col].sum()
        )
        if not grupos.empty:
            categorias = {categoria: float(valor_cat) for categoria, valor_cat in grupos.items() if pd.notna(valor_cat)}

    return {
        "status": "ok",
        "motivo": None,
        "valor": valor,
        "categorias": categorias,
        "periodos": periodos_disponiveis,
        "anos_sheets": anos_sheets,
    }


def _overview_km_totals(*, ano: int | None = None, mes: int | None = None, meses: list[int] | None = None) -> dict[str, float]:
    try:
        df_km = _apply_plate_categories(load_combustivel_km())
        df_km = _filter_by_period(df_km, ano=ano, mes=mes, meses=meses or [])
    except Exception:
        df_km = _empty(_COMBUSTIVEL_KM_COLUMNS)
    if df_km.empty or "Km Rodados" not in df_km:
        return {"total": 0.0, "transporte": 0.0, "vex": 0.0}

    work = df_km.copy()
    work["Km Rodados"] = pd.to_numeric(work["Km Rodados"], errors="coerce").fillna(0.0)
    if "Categoria" not in work.columns:
        work["Categoria"] = "Transporte"
    categorias = _normalize_categoria(work["Categoria"])
    return {
        "total": float(work["Km Rodados"].sum()),
        "transporte": float(work.loc[categorias == "transporte", "Km Rodados"].sum()),
        "vex": float(work.loc[categorias == "vex", "Km Rodados"].sum()),
    }


def _overview_transport_labor_totals(
    *,
    ano: int | None = None,
    mes: int | None = None,
    meses: list[int] | None = None,
) -> dict[str, object]:
    try:
        salarios = load_salarios_transporte()
    except Exception:
        salarios = _empty(_SALARIOS_TRANSPORTE_COLUMNS)

    periodos = (
        {
            str(periodo)
            for periodo in pd.to_datetime(salarios.get("Mes"), errors="coerce").dt.to_period("M").dropna().unique()
        }
        if "Mes" in salarios.columns
        else set()
    )
    salarios = _filter_by_period(salarios, ano=ano, mes=mes, meses=meses or [])
    salario_total = (
        float(pd.to_numeric(salarios.get("Valor"), errors="coerce").fillna(0.0).clip(lower=0).sum())
        if "Valor" in salarios.columns
        else 0.0
    )

    try:
        peso = load_peso()
        peso = _filter_by_period(peso, ano=ano, mes=mes, meses=meses or [])
        diarias = _ranking_freighter_daily_costs(peso)
    except Exception:
        diarias = pd.DataFrame(columns=["Data", "Mes", "PLACA", "Diaria", "Custo"])
    custo_freteiros = (
        float(pd.to_numeric(diarias.get("Custo"), errors="coerce").fillna(0.0).clip(lower=0).sum())
        if "Custo" in diarias.columns
        else 0.0
    )
    return {
        "salario_transporte": salario_total,
        "custo_freteiros": custo_freteiros,
        "periodos": sorted(periodos),
    }


def _overview_transport_operating_total(
    *,
    ano: int | None = None,
    mes: int | None = None,
    meses: list[int] | None = None,
) -> float:
    categories = ["Transporte", "Freteiro"]
    total = 0.0
    sources = (
        (load_combustivel, "Custo", True),
        (load_manutencao, "Custo", True),
        (load_pedagio, "Custo", True),
        (load_hoteis, "Valor", False),
    )
    for loader, value_column, requires_plate in sources:
        try:
            data = _apply_plate_categories(loader())
            if requires_plate:
                data = _ranking_filter_valid_plates(data)
            data = _ranking_filter_categories(data, categories)
            data = _filter_by_period(data, ano=ano, mes=mes, meses=meses or [])
        except Exception:
            continue
        if value_column not in data.columns:
            continue
        total += float(
            pd.to_numeric(data[value_column], errors="coerce")
            .fillna(0.0)
            .clip(lower=0)
            .sum()
        )
    return total


def _overview_transport_ranking_total(
    *,
    ano: int | None = None,
    meses: list[int] | None = None,
    periodos: list[str] | None = None,
    fallback: float = 0.0,
) -> float:
    meses = meses or []
    periodos = periodos or []
    ranking_months: list[str] = []
    if meses:
        if ano is not None:
            ranking_months = [f"{ano}-{month:02d}" for month in meses]
        else:
            selected_months = set(meses)
            ranking_months = [
                period
                for period in periodos
                if "-" in period and int(period.split("-")[1]) in selected_months
            ]

    params: dict[str, object] = {
        "categoria": ["Transporte", "Freteiro"],
        "incluir_hoteis": True,
    }
    if ano is not None:
        params["ano"] = ano
    if ranking_months:
        params["mes"] = ranking_months

    try:
        ranking_totals = data_frota(params).get("totais") or {}
        return float(ranking_totals.get("total") or 0.0)
    except Exception:
        return float(fallback)


def compute_overview_totals(*, ano: int | None = None, mes: int | None = None, meses_lista: list[int] | None = None) -> dict:
    ano = _parse_int(ano)
    mes = _parse_int(mes, min_value=1, max_value=12)
    meses_lista = list(meses_lista or [])
    if mes is not None and mes not in meses_lista:
        meses_lista.append(mes)

    areas = {
        "combustivel": (load_combustivel, agg_combustivel, "custo_total", "Custo", True),
        "manutencao": (load_manutencao, agg_manutencao, "custo_total", "Custo", True),
        "hoteis": (load_hoteis, agg_hoteis, "valor_total", "Valor", True),
        "pedagio": (load_pedagio, agg_pedagio, "custo_total", "Custo", True),
        "peso": (load_peso, agg_peso, "peso_total", "Peso", False),
    }

    overview_cache_datasets = (
        "combustivel",
        "combustivel_km",
        "manutencao",
        "hoteis",
        "pedagio",
        "peso",
        "placas",
        "salarios_transporte",
    )
    chave_cache = tuple(_CACHE_MAP[nome]["mtime"] for nome in overview_cache_datasets)
    use_cache = ano is None and mes is None and not meses_lista
    if use_cache and _OVERVIEW_CACHE["mtimes"] == chave_cache and _OVERVIEW_CACHE["dados"] is not None:
        return _OVERVIEW_CACHE["dados"]

    detalhes = {}
    total_geral = 0.0
    segmento_totais = defaultdict(float)
    periodos_unicos: set[str] = set()
    anos_extra: set[int] = set()
    max_workers = len(areas)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_safe_total, loader, aggregator, chave, valor_col, ano=ano, mes=mes, meses=meses_lista): nome
            for nome, (loader, aggregator, chave, valor_col, _is_money) in areas.items()
        }
        for future in concurrent.futures.as_completed(future_map):
            nome = future_map[future]
            is_money_area = areas[nome][4]
            resultado = future.result()
            detalhes[nome] = resultado
            if is_money_area and resultado["valor"] is not None:
                total_geral += resultado["valor"]
            if is_money_area:
                for categoria, valor in (resultado.get("categorias") or {}).items():
                    segmento_totais[categoria] += valor
            for periodo in resultado.get("periodos") or []:
                if periodo:
                    periodos_unicos.add(periodo)
            for ano_sheet in resultado.get("anos_sheets") or []:
                try:
                    anos_extra.add(int(ano_sheet))
                except (TypeError, ValueError):
                    continue

    segmentos_dict = {categoria: float(valor) for categoria, valor in segmento_totais.items()}
    periodos_ordenados = sorted(periodos_unicos)
    anos_disponiveis = sorted({int(p.split("-")[0]) for p in periodos_ordenados if "-" in p})
    if anos_extra:
        anos_disponiveis = sorted(set(anos_disponiveis) | anos_extra)
    periodos_base = periodos_ordenados
    if ano is not None:
        periodos_base = [periodo for periodo in periodos_ordenados if periodo.startswith(f"{ano}-")]
    meses_disponiveis = sorted({int(p.split("-")[1]) for p in periodos_base if "-" in p})

    detalhes["peso_total"] = float((detalhes.get("peso") or {}).get("valor") or 0.0)
    km_totais = _overview_km_totals(ano=ano, mes=mes, meses=meses_lista)
    detalhes["km_total"] = km_totais["total"]
    detalhes["km_transporte"] = km_totais["transporte"]
    detalhes["km_vex"] = km_totais["vex"]
    labor_totals = _overview_transport_labor_totals(
        ano=ano,
        mes=mes,
        meses=meses_lista,
    )
    periodos_unicos.update(labor_totals.get("periodos") or [])
    periodos_ordenados = sorted(periodos_unicos)
    anos_disponiveis = sorted({int(p.split("-")[0]) for p in periodos_ordenados if "-" in p})
    if anos_extra:
        anos_disponiveis = sorted(set(anos_disponiveis) | anos_extra)
    periodos_base = periodos_ordenados
    if ano is not None:
        periodos_base = [periodo for periodo in periodos_ordenados if periodo.startswith(f"{ano}-")]
    meses_disponiveis = sorted({int(p.split("-")[1]) for p in periodos_base if "-" in p})
    salario_transporte = float(labor_totals.get("salario_transporte") or 0.0)
    custo_freteiros = float(labor_totals.get("custo_freteiros") or 0.0)
    total_transporte_operacional = _overview_transport_operating_total(
        ano=ano,
        mes=mes,
        meses=meses_lista,
    )
    detalhes["segmentos"] = segmentos_dict
    detalhes["salario_transporte"] = salario_transporte
    detalhes["custo_freteiros"] = custo_freteiros
    detalhes["total_transporte_operacional"] = total_transporte_operacional
    total_transporte_fallback = total_transporte_operacional + salario_transporte + custo_freteiros
    total_transporte = _overview_transport_ranking_total(
        ano=ano,
        meses=meses_lista,
        periodos=periodos_ordenados,
        fallback=total_transporte_fallback,
    )
    total_vex = float(segmentos_dict.get("Vex", 0.0))
    detalhes["total_transporte"] = total_transporte
    detalhes["total_vex"] = total_vex
    detalhes["total_geral"] = total_transporte + total_vex
    detalhes["periodos_disponiveis"] = periodos_ordenados
    detalhes["anos_disponiveis"] = anos_disponiveis
    detalhes["meses_disponiveis"] = meses_disponiveis
    detalhes["filtro"] = {"ano": ano, "mes": mes, "meses": meses_lista}

    if use_cache:
        _OVERVIEW_CACHE["mtimes"] = tuple(_CACHE_MAP[nome]["mtime"] for nome in overview_cache_datasets)
        _OVERVIEW_CACHE["dados"] = detalhes
    return detalhes


def data_overview(params: dict | None = None) -> dict:
    params = params or {}
    ano = _parse_int(_param(params, "ano"))
    meses_lista = _parse_mes_int_list(params.get("mes"))
    mes = None if meses_lista else _parse_int(_param(params, "mes"), min_value=1, max_value=12)
    return compute_overview_totals(ano=ano, mes=mes, meses_lista=meses_lista)


def data_overview_options(params: dict | None = None) -> dict:
    params = params or {}
    ano = _parse_int(_param(params, "ano"))
    sources = (
        (load_combustivel, True),
        (load_manutencao, True),
        (load_hoteis, True),
        (load_pedagio, True),
        (load_peso, True),
        (load_salarios_transporte, False),
    )
    periodos: set[str] = set()
    anos_extra: set[int] = set()

    def _source_periods(source) -> tuple[set[str], set[int]]:
        loader, include_sheet_years = source
        try:
            data = loader()
        except Exception:
            return set(), set()
        source_periods: set[str] = set()
        if "Mes" in data.columns:
            parsed = pd.to_datetime(data["Mes"], errors="coerce").dt.to_period("M")
            source_periods = {str(period) for period in parsed.dropna().unique()}
        source_years: set[int] = set()
        for value in data.attrs.get("anos_sheets", []) if include_sheet_years else []:
            try:
                source_years.add(int(value))
            except (TypeError, ValueError):
                continue
        return source_periods, source_years

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(sources)) as pool:
        for source_periods, source_years in pool.map(_source_periods, sources):
            periodos.update(source_periods)
            anos_extra.update(source_years)

    periodos_ordenados = sorted(periodos)
    anos_disponiveis = sorted(
        {int(period.split("-")[0]) for period in periodos_ordenados if "-" in period}
        | anos_extra
    )
    periodos_base = (
        [period for period in periodos_ordenados if period.startswith(f"{ano}-")]
        if ano is not None
        else periodos_ordenados
    )
    meses_disponiveis = sorted(
        {int(period.split("-")[1]) for period in periodos_base if "-" in period}
    )
    meses_por_ano = {
        str(year): sorted(
            {
                int(period.split("-")[1])
                for period in periodos_ordenados
                if period.startswith(f"{year}-")
            }
        )
        for year in anos_disponiveis
    }
    return {
        "anos_disponiveis": anos_disponiveis,
        "meses_disponiveis": meses_disponiveis,
        "meses_por_ano": meses_por_ano,
    }


def main() -> None:
    from streamlit_app import main as streamlit_main

    os.environ.setdefault("JR_SKIP_WARM_CACHE", "1")
    streamlit_main()


if __name__ == "__main__":
    main()
