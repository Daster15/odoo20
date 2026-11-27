#!/usr/bin/env bash
# Creates fields used by ltech_doc_search in the odoo_docs core.
set -euo pipefail
SOLR=${SOLR:-http://localhost:8983/solr/odoo_docs}

curl -sf -X POST -H 'Content-type:application/json' "$SOLR/schema" -d '{
  "add-field": [
    {"name":"name",        "type":"text_general", "stored":true, "multiValued":false},
    {"name":"record_name", "type":"text_general", "stored":true, "multiValued":false},
    {"name":"content",     "type":"text_general", "stored":true, "multiValued":false,
     "storeOffsetsWithPositions":true},
    {"name":"res_model",   "type":"string", "stored":true},
    {"name":"res_id",      "type":"plong",  "stored":true},
    {"name":"project_id",  "type":"plong",  "stored":true},
    {"name":"company_id",  "type":"plong",  "stored":true},
    {"name":"mimetype",    "type":"string", "stored":true},
    {"name":"file_type",   "type":"string", "stored":true},
    {"name":"create_date", "type":"pdate",  "stored":true}
  ]
}'
echo "schema OK"
