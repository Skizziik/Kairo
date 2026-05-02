// Server contract types — must mirror app/villager/game.py serializers.

export interface ResourceSnap {
  type: string;
  amount: string;   // numeric as string (big numbers)
  cap: string;
}

export interface BuildingSnap {
  id: number;
  type: string;
  level: number;
  x: number;
  y: number;
  status: "active" | "building" | "upgrading";
  finish_at: string | null;
  pending_collect: Record<string, string>;
}

export interface QuestSnap {
  id: string;
  name: string;
  description: string;
  status: "active" | "completed" | "claimed";
  progress: Record<string, number>;
  rewards: Record<string, number>;
  claimed_at: string | null;
}

export interface UserSnap {
  tg_id: number;
  village_name: string;
  era: number;
  player_level: number;
  experience: number;
  gems_balance: string;
  first_name: string | null;
  username: string | null;
}

export interface StateSnap {
  user: UserSnap;
  resources: ResourceSnap[];
  buildings: BuildingSnap[];
  quests: QuestSnap[];
  pending_total: Record<string, string>;
  builder_slots: number;
  map_size: [number, number];
  tile_size: number;
  server_time: string;
}

export interface BuildingLevel {
  level: number;
  cost: Record<string, number>;
  build_time_seconds: number;
  output_per_hour: Record<string, number>;
  storage_bonus: Record<string, number>;
  icon: string;
}

export interface BuildingDef {
  name: string;
  size: [number, number];
  max_level: number;
  era: number;
  max_per_user: number;
  description: string;
  icon: string;
  is_demolishable: boolean;
  levels: BuildingLevel[];
}

export interface ResourceDef {
  name: string;
  icon: string;
  base_cap: number;
  color: string;
}

export interface QuestDef {
  name: string;
  description: string;
  trigger: string;
  target: Record<string, any>;
  rewards: Record<string, number>;
  next: string[];
  auto_start?: boolean;
}

export interface ConfigSnap {
  version: string;
  buildings: Record<string, BuildingDef>;
  resources: Record<string, ResourceDef>;
  quests: Record<string, QuestDef>;
  map_size: [number, number];
  tile_size: number;
}

export interface ApiResponse<T = any> {
  ok: boolean;
  data?: T;
  error?: string;
  message?: string;
  missing?: string;
}
