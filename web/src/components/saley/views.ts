// The views of the dashboard (docs/08 Shell and Views). The ids are the legacy rail `data-v`
// values (static/index.html), so `/#today` links keep working after the cut-over (D147).
// Each view has its title, a one-line subtitle, its legend (R5-Q01) and the features that live
// on it (R5-Q03, D164). T13–T16a replaced each placeholder body with the real page.
import {
  ActivityIcon,
  BanknoteIcon,
  BuildingIcon,
  CalendarCheckIcon,
  CalendarDaysIcon,
  ChartColumnIcon,
  CircleAlertIcon,
  ClipboardListIcon,
  HandshakeIcon,
  InboxIcon,
  LightbulbIcon,
  NewspaperIcon,
  ScaleIcon,
  SearchIcon,
  TargetIcon,
  UsersIcon,
  type LucideIcon,
} from "lucide-react";
import type { Feature } from "@/hooks/use-me";
import { W } from "@/words";
import type { LegendItem } from "./Legend";

export type Group = "plan" | "watch" | "team" | "admin" | "hidden";

export type ViewDef = {
  id: string;
  nav: string;
  title: string;
  sub: string;
  group: Group;
  icon: LucideIcon;
  legend: LegendItem[];
  features: readonly Feature[];
};

// Legend entries used on more than one page.
const DASH: LegendItem = { tag: "dash", tone: "neutral", name: "—", explain: W.lgDash };
const SOON: LegendItem = { tag: "soon", tone: "soon", name: W.soon, explain: W.lgSoon };
const STAGE: LegendItem = { tag: "stage", tone: "neutral", name: W.lgStage, explain: W.lgStageX };
// D201: one named colour per stage, the same on every page (tones.ts STAGE_TONES).
export const STAGES: LegendItem[] = [
  { tag: "stage-listed", tone: "stage-listed", name: W.stListed, explain: W.stListedX },
  { tag: "stage-contacted", tone: "stage-contacted", name: W.stContacted, explain: W.stContactedX },
  { tag: "stage-connected", tone: "stage-connected", name: W.stConnected, explain: W.stConnectedX },
  { tag: "stage-talking", tone: "stage-talking", name: W.stTalking, explain: W.stTalkingX },
  { tag: "stage-met", tone: "stage-met", name: W.stMet, explain: W.stMetX },
  { tag: "stage-qualified", tone: "stage-qualified", name: W.stQualified, explain: W.stQualifiedX },
  { tag: "stage-won", tone: "stage-won", name: W.stWon, explain: W.stWonX },
  { tag: "stage-parked", tone: "stage-parked", name: W.stParked, explain: W.stParkedX },
  { tag: "stage-lost", tone: "stage-lost", name: W.stLost, explain: W.stLostX },
];
const FUNNEL_STAGES = ["listed", "contacted", "connected", "talking", "met", "won"].map(
  (key) => STAGES.find((s) => s.tag === `stage-${key}`) as LegendItem,
);
const LATE: LegendItem = { tag: "late", tone: "bad", name: W.lgLate, explain: W.lgLateX };
const DUE_TODAY: LegendItem = {
  tag: "due-today",
  tone: "warn",
  name: W.lgDueToday,
  explain: W.lgDueTodayX,
};
const DUE_LATER: LegendItem = {
  tag: "due-later",
  tone: "neutral",
  name: W.lgDueLater,
  explain: W.lgDueLaterX,
};
const UP: LegendItem = { tag: "up", tone: "good", name: W.lgUp, explain: W.lgUpX };
const DOWN: LegendItem = { tag: "down", tone: "warn", name: W.lgDown, explain: W.lgDownX };
const NEW: LegendItem = { tag: "new", tone: "info", name: W.lgNew, explain: W.lgNewX };

export const VIEWS: ViewDef[] = [
  {
    id: "today",
    nav: W.navToday,
    title: W.navToday,
    sub: W.subToday,
    group: "plan",
    icon: CalendarCheckIcon,
    // R5 T13: the funnel colours, the task kinds and due tags, the card types, "—", soon.
    legend: [
      ...FUNNEL_STAGES,
      { tag: "task-meeting", tone: "touch-meet", name: W.kindMeeting, explain: W.kindMeetingX },
      { tag: "task-reconnect", tone: "info", name: W.kindReconnect, explain: W.kindReconnectX },
      { tag: "task-email", tone: "touch-email", name: W.kindEmail, explain: W.kindEmailX },
      { tag: "task-li", tone: "touch-li", name: W.kindLi, explain: W.kindLiX },
      { tag: "task-promise", tone: "touch-other", name: W.kindPromise, explain: W.kindPromiseX },
      { tag: "task-team", tone: "primary", name: W.kindTeam, explain: W.kindTeamX },
      LATE,
      DUE_TODAY,
      DUE_LATER,
      { tag: "no-date", tone: "neutral", name: W.noDate, explain: W.lgNoDateX },
      { tag: "news", tone: "info", name: W.lgNews, explain: W.lgNewsX },
      { tag: "event", tone: "primary", name: W.lgEvent, explain: W.lgEventX },
      { tag: "blocker", tone: "bad", name: W.lgBlocker, explain: W.lgBlockerX },
      { tag: "waiting", tone: "warn", name: W.lgWaiting, explain: W.lgWaitingX },
      DASH,
      SOON,
    ],
    features: ["find_people", "email_write", "mail_replies"],
  },
  {
    id: "goals",
    nav: W.navGoals,
    title: W.navGoals,
    sub: W.subGoals,
    group: "plan",
    icon: TargetIcon,
    legend: [
      { tag: "on-track", tone: "good", name: W.lgOnTrack, explain: W.lgOnTrackX },
      { tag: "at-risk", tone: "warn", name: W.lgAtRisk, explain: W.lgAtRiskX },
      { tag: "blocked", tone: "bad", name: W.lgBlocked, explain: W.lgBlockedX },
      { tag: "ring", tone: "primary", name: W.lgRing, explain: W.lgRingX },
      STAGE,
      ...STAGES,
      LATE,
      DUE_TODAY,
      DUE_LATER,
      DASH,
    ],
    features: [],
  },
  {
    id: "accounts",
    nav: W.navAccounts,
    title: W.navAccounts,
    sub: W.subAccounts,
    group: "plan",
    icon: BuildingIcon,
    legend: [
      { tag: "qualified", tone: "good", name: W.lgQualified, explain: W.lgQualifiedX },
      { tag: "engaged", tone: "info", name: W.lgEngaged, explain: W.lgEngagedX },
      { tag: "single", tone: "warn", name: W.lgSingle, explain: W.lgSingleX },
      STAGE,
      ...STAGES,
      { tag: "touch", tone: "touch-li", name: W.lgTouch, explain: W.lgTouchX },
      DASH,
    ],
    features: [],
  },
  {
    id: "people",
    nav: W.navPeople,
    title: W.navPeople,
    sub: W.subPeople,
    group: "plan",
    icon: UsersIcon,
    legend: [
      { tag: "p1", tone: "warn", name: W.lgP1, explain: W.lgP1X },
      STAGE,
      ...STAGES,
      { tag: "touch", tone: "touch-li", name: W.lgTouch, explain: W.lgTouchX },
      DASH,
    ],
    features: [],
  },
  {
    id: "suggest",
    nav: W.navSuggest,
    title: W.navSuggest,
    sub: W.subSuggest,
    group: "plan",
    icon: LightbulbIcon,
    legend: [NEW, SOON],
    features: ["find_people", "apollo"],
  },
  {
    id: "signals",
    nav: W.navSignals,
    title: W.navSignals,
    sub: W.subSignals,
    group: "watch",
    icon: NewspaperIcon,
    legend: [
      { tag: "cat-hiring", tone: "cat-1", name: W.catHiring, explain: W.catHiringX },
      { tag: "cat-funding", tone: "cat-2", name: W.catFunding, explain: W.catFundingX },
      { tag: "cat-launch", tone: "cat-3", name: W.catLaunch, explain: W.catLaunchX },
      { tag: "cat-research", tone: "cat-4", name: W.catResearch, explain: W.catResearchX },
      { tag: "cat-partner", tone: "cat-5", name: W.catPartner, explain: W.catPartnerX },
      {
        tag: "cat-opportunity",
        tone: "cat-7",
        name: W.catOpportunity,
        explain: W.catOpportunityX,
      },
      { tag: "cat-event", tone: "cat-6", name: W.catEvent, explain: W.catEventX },
      { tag: "ours", tone: "primary", name: W.lgOurs, explain: W.lgOursX },
      { tag: "important", tone: "warn", name: W.lgImportant, explain: W.lgImportantX },
      { tag: "verify", tone: "warn", name: W.lgVerify, explain: W.lgVerifyX },
      NEW,
    ],
    features: [],
  },
  {
    id: "blockers",
    nav: W.navBlockers,
    title: W.navBlockers,
    sub: W.subBlockers,
    group: "watch",
    icon: CircleAlertIcon,
    legend: [
      LATE,
      { tag: "complete", tone: "primary", name: W.lgComplete, explain: W.lgCompleteX },
      DASH,
    ],
    features: [],
  },
  {
    id: "events",
    nav: W.navEvents,
    title: W.navEvents,
    sub: W.subEvents,
    group: "watch",
    icon: CalendarDaysIcon,
    legend: [
      { tag: "registered", tone: "good", name: W.lgRegistered, explain: W.lgRegisteredX },
      NEW,
      SOON,
    ],
    features: ["speaker_match"],
  },
  {
    id: "asks",
    nav: W.navAsks,
    title: W.navAsks,
    sub: W.subAsks,
    group: "team",
    icon: InboxIcon,
    // R5 T16a: one tag per kind of item (inbox-rules.ts INBOX_KINDS).
    legend: [
      { tag: "card", tone: "primary", name: W.lgCard, explain: W.lgCardX },
      { tag: "confirm", tone: "info", name: W.lgConfirm, explain: W.lgConfirmX },
      { tag: "ask", tone: "neutral", name: W.lgAsk, explain: W.lgAskX },
      { tag: "reminder", tone: "warn", name: W.lgReminder, explain: W.lgReminderX },
      DASH,
      SOON,
    ],
    features: ["dms"],
  },
  {
    id: "commit",
    nav: W.navCommit,
    title: W.navCommit,
    sub: W.subCommit,
    group: "team",
    icon: HandshakeIcon,
    legend: [
      { tag: "open", tone: "info", name: W.lgOpen, explain: W.lgOpenX },
      { tag: "done", tone: "good", name: W.lgDone, explain: W.lgDoneX },
      { tag: "dropped", tone: "neutral", name: W.lgDropped, explain: W.lgDroppedX },
      LATE,
      DUE_TODAY,
      DUE_LATER,
      { tag: "no-date", tone: "neutral", name: W.cNoDue, explain: W.lgNoDateX },
      DASH,
    ],
    features: [],
  },
  {
    id: "decisions",
    nav: W.navDecisions,
    title: W.navDecisions,
    sub: W.subDecisions,
    group: "team",
    icon: ScaleIcon,
    legend: [
      { tag: "proposed", tone: "warn", name: W.lgProposed, explain: W.lgProposedX },
      { tag: "confirmed", tone: "good", name: W.lgConfirmed, explain: W.lgConfirmedX },
      { tag: "active", tone: "primary", name: W.lgActive, explain: W.lgActiveX },
      { tag: "undo-open", tone: "info", name: W.undoOpen, explain: W.lgUndoOpenX },
      { tag: "undone", tone: "neutral", name: W.lgUndone, explain: W.lgUndoneX },
      { tag: "undo-closed", tone: "neutral", name: W.undoClosed, explain: W.lgUndoClosedX },
      { tag: "undo-failed", tone: "bad", name: W.undoFailed, explain: W.lgUndoFailedX },
      { tag: "sync-pending", tone: "warn", name: W.syncPending, explain: W.lgSyncPendingX },
      { tag: "sync-held", tone: "warn", name: W.syncHeld, explain: W.lgSyncHeldX },
      { tag: "sync-done", tone: "good", name: W.syncDone, explain: W.lgSyncDoneX },
      { tag: "sync-failed", tone: "bad", name: W.syncFailed, explain: W.lgSyncFailedX },
      {
        tag: "sync-cancelled",
        tone: "neutral",
        name: W.syncCancelled,
        explain: W.lgSyncCancelledX,
      },
      DASH,
    ],
    features: [],
  },
  {
    id: "pack",
    nav: W.navPack,
    title: W.navPack,
    sub: W.subPack,
    group: "team",
    icon: ClipboardListIcon,
    legend: [UP, DOWN, DASH],
    features: [],
  },
  {
    id: "scorecard",
    nav: W.navScorecard,
    title: W.navScorecard,
    sub: W.subScorecard,
    group: "team",
    icon: ChartColumnIcon,
    legend: [UP, DOWN, DASH],
    features: [],
  },
  {
    id: "sources",
    nav: W.navSources,
    title: W.navSources,
    sub: W.subSources,
    group: "admin",
    icon: ActivityIcon,
    legend: [
      { tag: "ok", tone: "good", name: W.lgOk, explain: W.lgOkX },
      { tag: "failed", tone: "bad", name: W.lgFailed, explain: W.lgFailedX },
      { tag: "old", tone: "warn", name: W.lgOld, explain: W.lgOldX },
      { tag: "running", tone: "info", name: W.lgRunning, explain: W.lgRunningX },
      { tag: "queued", tone: "info", name: W.lgQueued, explain: W.lgQueuedX },
      { tag: "skipped", tone: "neutral", name: W.lgSkipped, explain: W.lgSkippedX },
      DASH,
      SOON,
    ],
    features: ["apollo"],
  },
  {
    id: "money",
    nav: W.navMoney,
    title: W.navMoney,
    sub: W.subMoney,
    group: "hidden",
    icon: BanknoteIcon,
    legend: [LATE, STAGE, ...STAGES, { ...DASH, explain: W.lgNoValue }],
    features: [],
  },
  {
    id: "search",
    nav: W.navSearch,
    title: W.navSearch,
    sub: W.searchHint,
    group: "hidden",
    icon: SearchIcon,
    legend: [STAGE, ...STAGES, { tag: "goal", tone: "primary", name: W.lgGoal, explain: W.lgGoalX }],
    features: [],
  },
];

export const RAIL_GROUPS: { group: Group; label: string }[] = [
  { group: "plan", label: W.grpPlan },
  { group: "watch", label: W.grpWatch },
  { group: "team", label: W.grpTeam },
  { group: "admin", label: W.grpAdmin },
];

const DEFAULT_VIEW = "today";

/** The view to show for a hash: unknown names and admin-only views fall back to Today. */
export function resolveView(name: string, admin: boolean): ViewDef {
  const found = VIEWS.find((v) => v.id === name);
  if (!found || (found.group === "admin" && !admin)) {
    return VIEWS.find((v) => v.id === DEFAULT_VIEW) as ViewDef;
  }
  return found;
}
