CREATE TABLE tenants (
 id text PRIMARY KEY, code text UNIQUE NOT NULL, name text NOT NULL, mode text NOT NULL,
 enabled boolean NOT NULL DEFAULT true, epoch bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE users (
 id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants(id),
 username text NOT NULL, display_name text NOT NULL, password_hash text NOT NULL,
 system_admin boolean NOT NULL DEFAULT false, active boolean NOT NULL DEFAULT true,
 must_change boolean NOT NULL DEFAULT true, version bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,username), UNIQUE(tenant_id,id)
);
CREATE TABLE org_nodes (
 id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants(id), parent_id text,
 name text NOT NULL, kind text NOT NULL CHECK(kind IN ('company','department','team')),
 active boolean NOT NULL DEFAULT true, version bigint NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,parent_id) REFERENCES org_nodes(tenant_id,id)
);
CREATE TABLE memberships (
 tenant_id text NOT NULL, user_id text NOT NULL, node_id text NOT NULL,
 role text NOT NULL CHECK(role IN ('employee','leader','boss')), active boolean NOT NULL DEFAULT true,
 PRIMARY KEY(user_id,node_id,role),
 FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,node_id) REFERENCES org_nodes(tenant_id,id)
);
CREATE TABLE sessions (
 id text PRIMARY KEY, tenant_id text NOT NULL, user_id text NOT NULL, user_version bigint NOT NULL,
 csrf_hash text NOT NULL, expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false,
 FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
);
CREATE TABLE login_attempts (
 key text PRIMARY KEY, count integer NOT NULL DEFAULT 0, locked_until timestamptz, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE documents (
 id text PRIMARY KEY, tenant_id text NOT NULL, node_id text NOT NULL, creator_id text NOT NULL,
 title text NOT NULL, scope text NOT NULL CHECK(scope IN ('company','org','selected')),
 level integer NOT NULL CHECK(level BETWEEN 1 AND 3), ai_allowed boolean NOT NULL DEFAULT false,
 download_allowed boolean NOT NULL DEFAULT false,
 state text NOT NULL CHECK(state IN ('draft','ingesting','review','published','deleted','failed')),
 current_version integer NOT NULL DEFAULT 1, acl_version bigint NOT NULL DEFAULT 1,
 error text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,node_id) REFERENCES org_nodes(tenant_id,id),
 FOREIGN KEY(tenant_id,creator_id) REFERENCES users(tenant_id,id)
);
CREATE TABLE document_versions (
 tenant_id text NOT NULL, document_id text NOT NULL, version integer NOT NULL,
 title text NOT NULL, body text NOT NULL, content_hash text NOT NULL, blob_name text,
 mime_type text NOT NULL DEFAULT 'text/plain', created_by text NOT NULL, policy jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(document_id,version),
 FOREIGN KEY(tenant_id,document_id) REFERENCES documents(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES users(tenant_id,id)
);
CREATE TABLE chunks (
 id text PRIMARY KEY, tenant_id text NOT NULL, document_id text NOT NULL, version integer NOT NULL,
 ordinal integer NOT NULL, page integer NOT NULL DEFAULT 0, text text NOT NULL,
 FOREIGN KEY(tenant_id,document_id) REFERENCES documents(tenant_id,id),
 FOREIGN KEY(document_id,version) REFERENCES document_versions(document_id,version),
 UNIQUE(document_id,version,ordinal)
);
CREATE TABLE document_grants (
 tenant_id text NOT NULL, document_id text NOT NULL, user_id text NOT NULL,
 effect text NOT NULL CHECK(effect IN ('allow','deny')), can_read boolean NOT NULL DEFAULT true,
 can_download boolean NOT NULL DEFAULT false, can_write boolean NOT NULL DEFAULT false,
 expires_at timestamptz, granted_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(document_id,user_id),
 FOREIGN KEY(tenant_id,document_id) REFERENCES documents(tenant_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
);
CREATE TABLE approvals (
 id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants(id), kind text NOT NULL,
 target_id text NOT NULL, payload jsonb NOT NULL, requested_by text NOT NULL,
 required_role text NOT NULL CHECK(required_role IN ('leader','boss')), node_id text NOT NULL,
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','stale')),
 decided_by text, comment text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
 decided_at timestamptz, FOREIGN KEY(tenant_id,requested_by) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,node_id) REFERENCES org_nodes(tenant_id,id)
);
CREATE TABLE jobs (
 id text PRIMARY KEY, tenant_id text NOT NULL, owner_id text NOT NULL, owner_version bigint NOT NULL,
 node_id text NOT NULL, kind text NOT NULL CHECK(kind IN ('ask','ingest')),
 question text NOT NULL DEFAULT '', input_level integer NOT NULL DEFAULT 1 CHECK(input_level BETWEEN 1 AND 3),
 payload jsonb NOT NULL DEFAULT '{}', state text NOT NULL DEFAULT 'queued',
 result jsonb, sources jsonb NOT NULL DEFAULT '[]', error text NOT NULL DEFAULT '',
 stage text NOT NULL DEFAULT '等待处理', progress integer NOT NULL DEFAULT 0,
 run_version bigint NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0,
 lease_until timestamptz, worker_id text, cancel_requested boolean NOT NULL DEFAULT false,
 idempotency_key text, fingerprint text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
 UNIQUE(tenant_id,owner_id,idempotency_key),
 FOREIGN KEY(tenant_id,owner_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,node_id) REFERENCES org_nodes(tenant_id,id),
 CHECK(state IN ('queued','running','review','completed','cancelled','failed','rejected','legacy'))
);
CREATE TABLE task_events (
 id bigserial PRIMARY KEY, tenant_id text NOT NULL, job_id text NOT NULL REFERENCES jobs(id),
 run_version bigint NOT NULL, stage text NOT NULL, progress integer NOT NULL, status text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE task_reviews (
 id bigserial PRIMARY KEY, tenant_id text NOT NULL, job_id text NOT NULL REFERENCES jobs(id),
 reviewer_id text NOT NULL, decision text NOT NULL, comment text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,reviewer_id) REFERENCES users(tenant_id,id)
);
CREATE TABLE audit_events (
 id bigserial PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants(id), actor_id text,
 action text NOT NULL, target_id text NOT NULL DEFAULT '', node_id text, level integer NOT NULL DEFAULT 1,
 system boolean NOT NULL DEFAULT false, detail jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE model_calls (
 id bigserial PRIMARY KEY, tenant_id text NOT NULL, job_id text NOT NULL REFERENCES jobs(id),
 role text NOT NULL, elapsed_ms integer NOT NULL, prompt_tokens integer NOT NULL DEFAULT 0,
 completion_tokens integer NOT NULL DEFAULT 0, outcome text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE legacy_archive (
 source text NOT NULL, source_id text NOT NULL, tenant_id text NOT NULL REFERENCES tenants(id),
 record jsonb NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(source,source_id)
);
CREATE INDEX idx_jobs_claim ON jobs(state,lease_until,created_at);
CREATE INDEX idx_jobs_owner ON jobs(tenant_id,owner_id,created_at DESC);
CREATE INDEX idx_docs_visible ON documents(tenant_id,state,node_id,level);
CREATE INDEX idx_chunks_doc ON chunks(tenant_id,document_id,version);
CREATE INDEX idx_grants_user ON document_grants(tenant_id,user_id,expires_at);
CREATE INDEX idx_audit_tenant ON audit_events(tenant_id,created_at DESC);
CREATE INDEX idx_sessions_user ON sessions(tenant_id,user_id);
