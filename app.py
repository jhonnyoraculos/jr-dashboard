from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
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
    "placas": "dashboard_placas",
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
_OVERVIEW_CACHE = {"mtimes": None, "dados": None}
_PLATE_REGISTRY_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
_PLACAS_CACHE = {"mtime": None, "df": None, "lock": threading.Lock()}
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
_PLACAS_COLUMNS = ["PLACA", "Categoria"]
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
    "placas": _PLACAS_COLUMNS,
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
    if dataset in {"placas", "combustivel", "manutencao", "pneus", "pedagio", "peso"}:
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
        targets = ["combustivel", "combustivel_km", "empilhadeira_horas", "manutencao", "pneus", "pedagio", "peso"]
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
                "Categoria" TEXT NOT NULL
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
            conn.execute(text(f"DELETE FROM {placas_table} WHERE \"PLACA\" = :placa"), {"placa": prepared["PLACA"]})
            conn.execute(
                text(f"INSERT INTO {placas_table} (\"PLACA\", \"Categoria\") VALUES (:placa, :categoria)"),
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
                conn.execute(text(f"DELETE FROM {placas_table} WHERE \"PLACA\" = :placa"), {"placa": prepared["PLACA"]})
                conn.execute(
                    text(f"INSERT INTO {placas_table} (\"PLACA\", \"Categoria\") VALUES (:placa, :categoria)"),
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
                conn.execute(text(f"DELETE FROM {placas_table} WHERE \"PLACA\" = :placa"), {"placa": prepared["PLACA"]})
                conn.execute(
                    text(f"INSERT INTO {placas_table} (\"PLACA\", \"Categoria\") VALUES (:placa, :categoria)"),
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


def rename_plate(old_plate, new_plate, categoria: str) -> str:
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
        for dataset in ("combustivel", "combustivel_km", "empilhadeira_horas", "manutencao", "pneus", "pedagio", "peso"):
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

        placas_table = _quote_identifier(DB_TABLES["placas"])
        conn.execute(text(f"DELETE FROM {placas_table} WHERE \"PLACA\" IN (:old_plate, :new_plate)"), {"old_plate": old_value, "new_plate": new_value})
        conn.execute(
            text(f"INSERT INTO {placas_table} (\"PLACA\", \"Categoria\") VALUES (:placa, :categoria)"),
            {"placa": new_value, "categoria": categoria_value},
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
    for loader in (load_combustivel, load_manutencao, load_pneus, load_pedagio, load_peso):
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
    return grouped.sort_values("PLACA").reset_index(drop=True)


def load_placas() -> pd.DataFrame:
    version = (
        _db_version("placas"),
        _db_version("combustivel"),
        _db_version("manutencao"),
        _db_version("pedagio"),
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
    grouped = df.dropna(subset=["PLACA"]).groupby("PLACA").size()
    return {str(placa): int(valor or 0) for placa, valor in grouped.items()}


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


def _ranking_weight_dominance_by_city(df: pd.DataFrame, placas: list[str] | None = None) -> dict:
    if df.empty or not {"Cidade", "PLACA", "Peso"}.issubset(df.columns):
        return {"labels": [], "values": [], "city_counts": [], "cidades": []}

    work = df[["Cidade", "PLACA", "Peso"]].copy()
    work["Cidade"] = work["Cidade"].astype("string").str.strip().str.upper()
    work["PLACA"] = work["PLACA"].astype("string").str.strip()
    work["Peso"] = pd.to_numeric(work["Peso"], errors="coerce").fillna(0.0)
    work = work[
        (work["Cidade"].notna())
        & (work["Cidade"] != "")
        & (work["PLACA"].notna())
        & (work["PLACA"] != "")
        & (work["Peso"] > 0)
    ]
    if work.empty:
        return {"labels": [], "values": [], "city_counts": [], "cidades": []}

    grouped = work.groupby(["Cidade", "PLACA"], as_index=False)["Peso"].sum()
    city_totals = grouped.groupby("Cidade", as_index=False)["Peso"].sum().rename(columns={"Peso": "PesoCidade"})
    grouped = grouped.merge(city_totals, on="Cidade", how="left")
    grouped["Participacao"] = grouped.apply(
        lambda row: float(row["Peso"] / row["PesoCidade"] * 100) if row["PesoCidade"] else 0.0,
        axis=1,
    )
    placas = [str(placa).strip() for placa in (placas or []) if str(placa).strip()]
    if placas:
        selected = grouped[grouped["PLACA"].isin(placas)].copy()
    else:
        selected = grouped.sort_values(["Cidade", "Peso", "PLACA"], ascending=[True, False, True]).drop_duplicates("Cidade", keep="first").copy()

    if selected.empty:
        return {"labels": [], "values": [], "city_counts": [], "cidades": []}

    by_plate = selected.groupby("PLACA", as_index=False).agg(Peso=("Peso", "sum"), Cidades=("Cidade", "count"))
    by_plate = by_plate.sort_values(["Peso", "Cidades", "PLACA"], ascending=[False, False, True])
    cities = selected.sort_values(["Peso", "Cidade", "PLACA"], ascending=[False, True, True])
    return {
        "labels": [str(item) for item in by_plate["PLACA"].tolist()],
        "values": [round(float(item), 3) for item in by_plate["Peso"].tolist()],
        "city_counts": [int(item) for item in by_plate["Cidades"].tolist()],
        "cidades": [
            {
                "cidade": str(row["Cidade"]),
                "placa": str(row["PLACA"]),
                "peso": round(float(row["Peso"]), 3),
                "peso_cidade": round(float(row["PesoCidade"]), 3),
                "participacao": round(float(row["Participacao"]), 2),
            }
            for _, row in cities.iterrows()
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
    incluir_hoteis = _truthy_param(params.get("incluir_hoteis"))
    placas = _ranking_parse_plate_list(params.get("placa"))
    ordenar_por = str(_param(params, "ordenar_por") or "total").strip().lower()

    df_comb = _ranking_filter_valid_plates(_apply_plate_categories(load_combustivel()))
    df_manu = _ranking_filter_valid_plates(_apply_plate_categories(load_manutencao()))
    df_ped = _ranking_filter_valid_plates(_apply_plate_categories(load_pedagio()))
    df_km = _ranking_filter_valid_plates(_apply_plate_categories(load_combustivel_km()))
    df_peso = _ranking_filter_valid_plates(_apply_plate_categories(load_peso()))
    df_hoteis = _apply_plate_categories(load_hoteis())

    source_frames = [df_comb, df_manu, df_ped, df_peso]
    categoria_frames = [_ranking_filter_categories(df, categorias_filtro) for df in source_frames]
    df_comb_base, df_manu_base, df_ped_base, df_peso_base = categoria_frames
    df_km_base = _ranking_filter_categories(df_km, categorias_filtro)
    df_hoteis_base = _ranking_filter_categories(df_hoteis, categorias_filtro)

    anos_disponiveis: set[int] = set()
    year_frames = [df_comb_base, df_manu_base, df_ped_base, df_km_base, df_peso_base]
    if incluir_hoteis:
        year_frames.append(df_hoteis_base)
    for df_src in year_frames:
        anos_disponiveis.update(_unique_years(df_src))
        anos_disponiveis.update(df_src.attrs.get("anos_sheets", []))

    month_frames = [df_comb_base, df_manu_base, df_ped_base, df_km_base, df_peso_base]
    if incluir_hoteis:
        month_frames.append(df_hoteis_base)
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
    df_manu = _apply_period(df_manu_base)
    df_ped = _apply_period(df_ped_base)
    df_km = _apply_period(df_km_base)
    df_peso = _apply_period(df_peso_base)
    df_hoteis_period = _apply_period(df_hoteis_base) if incluir_hoteis else df_hoteis_base.iloc[0:0].copy()

    plate_source = [df[["PLACA"]] for df in (df_comb, df_manu, df_ped, df_km, df_peso) if not df.empty and "PLACA" in df.columns]
    placas_disponiveis = _unique_sorted(pd.concat(plate_source, ignore_index=True), "PLACA") if plate_source else []
    df_peso_dominancia = df_peso.copy()

    df_comb = _ranking_filter_plates(df_comb, placas)
    df_manu = _ranking_filter_plates(df_manu, placas)
    df_ped = _ranking_filter_plates(df_ped, placas)
    df_km = _ranking_filter_plates(df_km, placas)
    df_peso = _ranking_filter_plates(df_peso, placas)
    dominancia_peso = _ranking_weight_dominance_by_city(df_peso_dominancia, placas)

    categorias = set()
    for df_src in source_frames:
        categorias.update(_unique_sorted(df_src, "Categoria"))

    category_map = _ranking_category_map(df_comb_base, df_manu_base, df_ped_base, df_peso_base)
    total_comb = _ranking_sum_by_plate(df_comb, "Custo")
    total_manu = _ranking_sum_by_plate(df_manu, "Custo")
    total_ped = _ranking_sum_by_plate(df_ped, "Custo")
    peso_map = _ranking_sum_by_plate(df_peso, "Peso")
    valor_peso_map = _ranking_sum_by_plate(df_peso, "Valor")
    total_hoteis = float(pd.to_numeric(df_hoteis_period.get("Valor"), errors="coerce").fillna(0).sum()) if incluir_hoteis and "Valor" in df_hoteis_period.columns else 0.0
    mensal_total_frames = [(df_comb, "Custo"), (df_manu, "Custo"), (df_ped, "Custo")]
    if incluir_hoteis:
        mensal_total_frames.append((df_hoteis_period, "Valor"))
    mensal_total = _ranking_monthly_sum(mensal_total_frames, "Valor")
    mensal_peso = _ranking_monthly_sum([(df_peso, "Peso")], "Peso")
    mensal_km = _ranking_monthly_km(df_km, df_comb)
    mensal_litros = _ranking_monthly_sum([(df_comb, "Litros")], "Litros")
    litros_map = _ranking_sum_by_plate(df_comb, "Litros")
    km_override_map = _ranking_sum_by_plate(df_km, "Km Rodados")
    abastecimentos_map = _ranking_count_by_plate(df_comb)
    servicos_map = _ranking_count_by_plate(df_manu)
    pedagio_count_map = _ranking_count_by_plate(df_ped)

    placa_set = set(total_comb) | set(total_manu) | set(total_ped) | set(litros_map) | set(km_override_map) | set(peso_map)
    ranking = []
    for placa in sorted(placa_set):
        combustivel_total = total_comb.get(placa, 0.0)
        manutencao_total = total_manu.get(placa, 0.0)
        pedagio_total = total_ped.get(placa, 0.0)
        peso_total = peso_map.get(placa, 0.0)
        valor_peso_total = valor_peso_map.get(placa, 0.0)
        total = combustivel_total + manutencao_total + pedagio_total
        km_total = km_override_map.get(placa, 0.0)
        litros_total = litros_map.get(placa, 0.0)
        lancamentos = abastecimentos_map.get(placa, 0) + servicos_map.get(placa, 0) + pedagio_count_map.get(placa, 0)
        ranking.append(
            {
                "placa": placa,
                "categoria": category_map.get(placa, "Transporte"),
                "total": round(total, 2),
                "combustivel": round(combustivel_total, 2),
                "manutencao": round(manutencao_total, 2),
                "pedagio": round(pedagio_total, 2),
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

    sort_key = {
        "total": "total",
        "combustivel": "combustivel",
        "manutencao": "manutencao",
        "pedagio": "pedagio",
        "peso": "peso_total",
        "valor_entregas": "valor_peso",
    }.get(ordenar_por, "combustivel")
    ranking.sort(key=lambda row: (row.get(sort_key, 0.0), row.get("total", 0.0), row.get("placa", "")), reverse=True)
    for index, row in enumerate(ranking, start=1):
        row["rank"] = index

    total_km = sum(row["km_total"] for row in ranking)
    total_litros = sum(row["litros_total"] for row in ranking)
    total_gasto_placas = sum(row["total"] for row in ranking)
    total_gasto = total_gasto_placas + total_hoteis
    total_placas = len(ranking)
    periodos_media = set(meses) if meses else {str(mes) for mes in meses_disponiveis if str(mes).strip()}
    total_meses = len(periodos_media)

    return {
        "anos": sorted(anos_disponiveis),
        "meses": meses_disponiveis,
        "categorias": sorted(categorias),
        "placas": placas_disponiveis,
        "ordenar_por": sort_key,
        "ranking": ranking,
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
            "manutencao": round(sum(row["manutencao"] for row in ranking), 2),
            "pedagio": round(sum(row["pedagio"] for row in ranking), 2),
            "hoteis": round(total_hoteis, 2),
            "inclui_hoteis": incluir_hoteis,
            "peso_total": round(sum(row["peso_total"] for row in ranking), 3),
            "valor_peso": round(sum(row["valor_peso"] for row in ranking), 2),
            "km_total": round(total_km, 2),
            "litros_total": round(total_litros, 2),
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

    overview_cache_datasets = ("combustivel", "combustivel_km", "manutencao", "hoteis", "pedagio", "peso")
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

    detalhes["total_geral"] = float(total_geral)
    detalhes["peso_total"] = float((detalhes.get("peso") or {}).get("valor") or 0.0)
    km_totais = _overview_km_totals(ano=ano, mes=mes, meses=meses_lista)
    detalhes["km_total"] = km_totais["total"]
    detalhes["km_transporte"] = km_totais["transporte"]
    detalhes["km_vex"] = km_totais["vex"]
    detalhes["segmentos"] = segmentos_dict
    detalhes["total_transporte"] = segmentos_dict.get("Transporte", 0.0)
    detalhes["total_vex"] = segmentos_dict.get("Vex", 0.0)
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


def main() -> None:
    from streamlit_app import main as streamlit_main

    os.environ.setdefault("JR_SKIP_WARM_CACHE", "1")
    streamlit_main()


if __name__ == "__main__":
    main()
