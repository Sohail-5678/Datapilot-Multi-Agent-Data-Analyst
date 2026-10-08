/** API shapes (SPEC §12). Kept loose where the backend is the source of truth. */

export interface DatabaseInfo {
  db_id: string;
  title: string;
  subtitle: string;
  description: string;
  tables: number;
  columns: number;
  rows: number;
  source: string;
  source_url: string;
  license: string;
  examples: string[];
  /** "Your data" datasets only */
  uploaded?: boolean;
  files?: { name: string; bytes: number }[];
  table_details?: { name: string; original: string; source: string; rows: number; truncated: boolean; columns: { name: string; original: string; type: string; pii: boolean }[] }[];
  pii_columns?: string[];
  truncated?: string[];
  expires_at?: string | null;
  created_at?: string | null;
}

export interface DatabasesResponse {
  databases: DatabaseInfo[];
  uploaded?: DatabaseInfo[];
}

export const isUploadId = (id: string) => /^u_[0-9a-f]{10}$/.test(id);

export interface ColumnInfo {
  name: string;
  type: string;
  pk: boolean;
  fk: { table: string; column: string } | null;
  description: string;
  value_description: string;
  samples: string[];
  pii: boolean;
}

export interface TableInfo {
  name: string;
  rows: number;
  description: string;
  columns: ColumnInfo[];
}

export interface SchemaInfo {
  db_id: string;
  title: string;
  description: string;
  tables: TableInfo[];
  erd: string;
  examples: string[];
  source: string;
  license: string;
}

export interface ThreadInfo {
  id: string;
  db_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlanStep {
  goal: string;
  needs_sql: boolean;
  needs_analysis: boolean;
}

export interface CandidateInfo {
  id: string;
  strategy: string;
  status: string;
  row_count?: number | null;
  repairs?: number;
  error?: string | null;
  model?: string | null;
  sql?: string | null;
  result_hash?: string | null;
}

export interface TableResult {
  data_ref: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  step_idx: number;
}

export interface ChosenInfo {
  step_idx: number;
  sql: string;
  confidence: "High" | "Medium" | "Low";
  reasons: string[];
  agree: number;
  of: number;
}

export interface ChartInfo {
  spec: Record<string, unknown>;
  data_ref: string;
  source: string;
  step_idx: number;
}

export interface Grounding {
  checked: number;
  removed: string[];
  ok: boolean;
  rewritten?: boolean;
}

export interface Metrics {
  llm_calls: number;
  tool_calls: number;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
  list_price_cost_usd: number;
}

export interface Span {
  span_id: string;
  parent_id: string | null;
  kind: string;
  name: string;
  started_at: string;
  duration_ms: number;
  provider: string | null;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  status: string;
  error: string | null;
  input_redacted: Record<string, unknown>;
  output_redacted: Record<string, unknown>;
  attributes: Record<string, unknown>;
}

export interface AnalysisInfo {
  ok: boolean;
  code?: string;
  result?: unknown;
  stdout?: string;
  error?: string | null;
  duration_ms?: number | null;
  ran_in?: string;
  rows?: number;
}

export interface RunRecord {
  id: string;
  thread_id: string;
  db_id: string;
  question: string;
  status: string;
  confidence: string | null;
  chosen_sql: string | null;
  row_count: number | null;
  answer: string | null;
  chart: ChartInfo | null;
  grounding: Grounding | null;
  llm_calls: number;
  tokens_in: number;
  tokens_out: number;
  list_price_cost_usd: number | string;
  latency_ms: number | null;
  profile_version: string | null;
  created_at: string;
  detail: {
    plan?: (PlanStep & { needs_chart?: boolean })[];
    clarification?: string | null;
    steps?: {
      step_idx: number;
      goal: string;
      sql: string | null;
      row_count: number;
      truncated: boolean;
      confidence: string;
      reasons: string[];
      data_ref: string;
      candidates: CandidateInfo[];
      ok: boolean;
      columns: string[];
    }[];
    analysis?: AnalysisInfo | null;
    notes?: string[];
    metrics?: Metrics;
    end_state?: Record<string, unknown>;
  } | null;
  spans?: Span[];
  feedback?: { thumbs: number; comment: string | null } | null;
  live?: boolean;
}

export interface ApiErrorBody {
  error: { code: string; message: string; retry_after?: number };
}
