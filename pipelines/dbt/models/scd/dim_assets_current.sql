select asset_id, plant, model, criticality, install_date, rated_flow_m3h, rated_kw, dbt_valid_from as valid_from
from {{ ref('assets_snapshot') }}
where dbt_valid_to is null
