export type Page<T> = { items: T[]; next_cursor?: string | null };
export type User = {
  id: string;
  username: string;
  display_name: string;
  role: string;
  active: boolean;
};
export type Capabilities = {
  mode: "local" | "cloud";
  local_import: boolean;
  local_sources: boolean;
  server_sync: boolean;
  comments: boolean;
  favorites: boolean;
  user?: User;
};
export type Session = {
  id: string;
  title: string;
  original_title?: string;
  owner_id: string;
  owner_name?: string;
  project?: string;
  source_kind?: string;
  status?: string;
  current_revision?: string;
  event_count?: number;
  round_count?: number;
  updated_at: number;
  sync_status?: string;
  comment_count?: number;
  favorite?: boolean;
  source_id?: string;
  favorite_revision_id?: string;
  favorite_event_id?: string;
  favorite_round?: number;
  favorite_seq?: number;
};
export type Source = {
  id: string;
  title: string;
  source_kind: string;
  project?: string;
  path?: string;
  status?: string;
  session_id?: string;
};
export type Round = {
  number: number;
  start_seq: number;
  end_seq: number;
  preview: string;
  count: number;
};
export type EventRecord = {
  id: string;
  seq: number;
  round_number: number;
  event: Record<string, unknown>;
};
export type Job = {
  id: string;
  kind: string;
  state: string;
  progress: number;
  total: number;
  error?: string;
  session_id?: string;
  created_at: number;
  updated_at: number;
};
export type Comment = {
  id: string;
  user_id?: string;
  owner_id?: string;
  owner_name?: string;
  author_name?: string;
  display_name?: string;
  text: string;
  revision_id: string;
  event_id?: string;
  created_at: number;
};
export type Favorite = {
  id: string;
  session_id: string;
  revision_id?: string;
  event_id?: string;
  event_seq?: number;
  round_number?: number;
  session?: Session;
};
export type Invite = {
  id: string;
  token?: string;
  invite_url?: string;
  expires_at: number;
  used_at?: number;
  used_by?: string;
  revoked?: boolean;
};
export type Revision = {
  id: string;
  created_at: number;
  status: string;
  event_count?: number;
};
export type Remote = {
  url?: string;
  server_url?: string;
  connected?: boolean;
  user?: User;
  status?: string;
};
