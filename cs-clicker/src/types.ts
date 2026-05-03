// Mirrors app/clicker/game.py serializers.

export interface UserSnap {
  tg_id: number;
  first_name: string | null;
  username: string | null;
  level: number;
  max_level: number;
  checkpoint: number;
  cash: string;
  casecoins: string;
  glory: string;
  bp_xp: string;
  click_damage: string;
  auto_dps: string;
  crit_chance: string;
  crit_multiplier: string;
  luck: string;
  prestige_count: number;
  artifact_slots: number;
  bosses_killed: number;
  chests_opened: number;
  total_damage: string;
}

export interface CombatSnap {
  enemy_hp: string;
  enemy_max_hp: string;
  is_boss: boolean;
  timer_ends_at: string | null;
}

export interface LevelMeta {
  level: number;
  is_boss: boolean;
  location_name: string;
  location_bg: string;
  enemy_sprite: string;
  enemy_name: string | null;
  boss_flavor: string | null;
  next_boss_level: number;
}

export interface UpgradeOwn {
  kind: string;
  slot_id: string;
  level: number;
}

export interface InventoryItem {
  id: number;
  kind: "chest" | "artifact" | "mythic";
  item_id: string;
  rarity: string | null;
  equipped_slot: number | null;
  metadata: Record<string, any>;
}

export interface BusinessState {
  id: string;
  level: number;
  unlock_level: number;
  resource: string;
  rate_per_sec: string;
  tap_yield: string;
  pending: string;
  collectable_now?: string;
  upgrade_cost: string;
  upgrade_resource_cost: Record<string, string>;
  idle_consumption_per_sec?: Record<string, string>;
  branches: Record<string, number>;
  branch_bonuses: Record<string, number>;
}

export interface BusinessBranchDef {
  id: string;
  name: string;
  icon: string;
  max_level: number;
  base_cost: number;
  cost_resource?: string | null;             // legacy single-resource
  cost_per_level?: number;                   // legacy
  cost_resources?: Record<string, number>;   // new multi-resource
  effect: string;
  per_level: number;
  desc?: string;
}

export interface StateSnap {
  user: UserSnap;
  combat: CombatSnap;
  level_meta: LevelMeta;
  upgrades: UpgradeOwn[];
  inventory: InventoryItem[];
  resources: Record<string, string>;
  businesses: BusinessState[];
  prestige_nodes: Record<string, number>;
  server_time: string;
}

export interface PrestigeNodeDef {
  id: string;
  name: string;
  desc: string;
  max_level: number;
  cost_per_level: number[];
  effect: Record<string, any>;
  tier: number;
  category: string;
}

export interface BusinessDef {
  id: string;
  name: string;
  resource: string;
  unlock_level: number;
  base_idle_per_sec: number;
  base_tap_yield: number;
  base_upgrade_cost: number;
  icon: string;
  emoji: string;
}

export interface ResourceMeta {
  name: string;
  icon: string;
  emoji: string;
  color: string;
}

export interface WeaponDef {
  id: string;
  name: string;
  base_dmg: number;
  base_cost: number;
  unlock_level: number;
  max_level: number;
  icon: string;
  requires_boss_kill?: number;
}

export interface MercDef {
  id: string;
  name: string;
  role: string;
  base_dps: number;
  base_cost: number;
  unlock_level: number;
  max_level: number;
  icon: string;
}

export interface LocationDef {
  id: number;
  name: string;
  level_range: [number, number];
  bg: string;
  enemies: string[];
}

export interface BossDef {
  id: string;
  level: number;
  name: string;
  chest: string;
  flavor: string;
  icon: string;
  guaranteed_artifact?: boolean;
  guaranteed_mythic?: boolean;
}

export interface ChestDef {
  name: string;
  icon: string;
  rarity_color: string;
  rolls: any;
}

export interface ArtifactDef {
  id: string;
  name: string;
  rarity: string;
  icon: string;
  effect: Record<string, any>;
}

export interface MythicDef {
  id: string;
  name: string;
  icon: string;
  effect: Record<string, any>;
}

export interface CritLuckDef {
  id: string;
  name: string;
  base_cost: number;
  unlock_level: number;
  max_level: number;
  per_level_pct: number;
  icon: string;
}

export interface PermitDef {
  id: string;
  name: string;
  base_cost: number;
  casecoin_cost: number;
  unlock_level: number;
  max_level: number;
  icon: string;
  desc?: string;
}

export interface ConfigSnap {
  version: string;
  weapons: WeaponDef[];
  mercs: MercDef[];
  locations: LocationDef[];
  bosses: BossDef[];
  chests: Record<string, ChestDef>;
  artifacts: ArtifactDef[];
  mythics: MythicDef[];
  crit_luck: { crit_chance: CritLuckDef[]; crit_damage: CritLuckDef[]; luck: CritLuckDef[] };
  businesses: BusinessDef[];
  resources_meta: Record<string, ResourceMeta>;
  prestige_tree: PrestigeNodeDef[];
  business_tree: Record<string, BusinessBranchDef[]>;
  permits?: PermitDef[];
  constants: {
    level_time_normal: number;
    level_time_boss: number;
    hp_base: number;
    hp_growth: number;
    hp_boss_mult: number;
    coin_drop_ratio: number;
    boss_coin_mult: number;
    cost_growth: number;
    res_cost_growth?: number;
    damage_per_level: number;
    tap_rate_base?: number;
    checkpoint_every: number;
    business_idle_cap_hours: number;
  };
}

export interface ApiResponse<T = any> {
  ok: boolean;
  data?: T;
  error?: string;
  message?: string;
  needed?: string;
  unlock_level?: number;
  resource?: string;
  have?: string;
}

export interface BossMechanicEvent {
  type: "heal" | "timer_drain" | "click_debuff" | "silence_auto" | "phase_heal" | "shield";
  shout: string;
  amount?: string;
  drained_sec?: number;
  pct?: number;
  duration_sec?: number;
  phase?: number;
}

export interface TapResult {
  state: StateSnap;
  tap_damage: string;
  auto_damage: string;
  crits: number;
  killed: boolean;
  enemy_hp?: string;
  timeout?: boolean;
  new_level?: number;
  coin_reward?: string;
  was_boss?: boolean;
  chest_dropped?: string;
  artifact_dropped?: ArtifactDef | MythicDef | null;
  boss_mechanics?: BossMechanicEvent[];
  gas_dropped?: number;
  mini_chests?: number;
  bonus_chest?: string;
}

export interface OpenChestResult {
  state: StateSnap;
  tier: string;
  cash: string;
  casecoins: number;
  artifact: ArtifactDef | null;
  mythic: MythicDef | null;
  resources?: Record<string, string>;
}

export interface LeaderboardEntry {
  tg_id: number;
  first_name: string | null;
  username: string | null;
  score: string;
  max_level: number;
  prestige_count: number;
}
