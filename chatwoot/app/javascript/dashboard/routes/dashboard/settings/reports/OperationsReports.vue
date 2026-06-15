<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useStore } from 'vuex';
import { formatDistanceToNowStrict } from 'date-fns';

import ConversationApi from 'dashboard/api/inbox/conversation';
import ReportHeader from './components/ReportHeader.vue';
import { frontendURL } from 'dashboard/helper/URLHelper';

// Operations dashboard — the "what's going on right now" view that the
// existing Reports section was missing. Shows live counts per team across
// Open / Pending / Snoozed states, total resolved-today, and a list of the
// longest-open conversations.
//
// Every cell is a click-through into Chatwoot's existing conversations list
// with the right filter pre-applied (team_conversations + status filter via
// query param). We intentionally do NOT build a new list view — agents
// already know how to drive Chatwoot's conversation list, so the dashboard's
// job is just to surface the counts and hand them off.
//
// Counts are fetched via ConversationApi.meta() — that endpoint returns
// just the count metadata without paginating actual conversation payloads,
// so a full refresh is cheap (1 round-trip per cell, run in parallel).

const STATUSES = ['open', 'pending', 'snoozed'];
const HERO_STATUSES = ['open', 'pending', 'snoozed'];
const REFRESH_INTERVAL_MS = 60_000;
// Cap on how many "longest open" rows we surface. Anything more would be
// noise — agents only ever scan the top of this list.
const LONGEST_OPEN_LIMIT = 10;

const store = useStore();
const router = useRouter();

const accountId = computed(() => store.getters.getCurrentAccountId);
const teams = computed(() => store.getters['teams/getTeams'] || []);

// Status -> count (hero row). Stored as a plain object keyed by status name.
const heroCounts = ref({ open: null, pending: null, snoozed: null });
// teamId -> { open, pending, snoozed }. null = still loading.
const teamCounts = ref({});
const resolvedTodayCount = ref(null);
const longestOpen = ref([]);

const loadingHero = ref(false);
const loadingGrid = ref(false);
const loadingLongest = ref(false);
const errorMessage = ref('');

let refreshTimer = null;

// ── Data fetching ─────────────────────────────────────────────────────────
// Best-effort everywhere: if a cell's count call fails we leave it as `?`
// rather than failing the whole dashboard. The Reports section is read-only,
// so a transient API blip never blocks any agent workflow.
const fetchCount = async params => {
  try {
    const { data } = await ConversationApi.meta(params);
    // Chatwoot's meta endpoint returns { data: { meta: { all_count, ... } } }.
    return data?.data?.meta?.all_count ?? data?.meta?.all_count ?? 0;
  } catch (err) {
    return null;
  }
};

const fetchHero = async () => {
  loadingHero.value = true;
  const promises = HERO_STATUSES.map(s => fetchCount({ status: s }));
  // Resolved-today: fetch one page of resolved, time-bounded to the last 24h
  // via updated_within (seconds). meta() doesn't take updated_within, so we
  // hit the list endpoint and read its meta out instead.
  const resolvedTodayPromise = (async () => {
    try {
      const { data } = await ConversationApi.get({
        status: 'resolved',
        updatedWithin: 24 * 60 * 60,
        page: 1,
      });
      return data?.data?.meta?.all_count ?? 0;
    } catch (err) {
      return null;
    }
  })();
  const results = await Promise.all([...promises, resolvedTodayPromise]);
  HERO_STATUSES.forEach((s, i) => {
    heroCounts.value[s] = results[i];
  });
  resolvedTodayCount.value = results[results.length - 1];
  loadingHero.value = false;
};

const fetchTeamGrid = async () => {
  if (!teams.value.length) {
    teamCounts.value = {};
    return;
  }
  loadingGrid.value = true;
  // One meta() call per (team, status) — runs all in parallel.
  const tasks = [];
  for (const team of teams.value) {
    for (const status of STATUSES) {
      tasks.push(
        fetchCount({ teamId: team.id, status }).then(count => ({
          teamId: team.id,
          status,
          count,
        }))
      );
    }
  }
  const results = await Promise.all(tasks);
  const next = {};
  for (const team of teams.value) {
    next[team.id] = { open: null, pending: null, snoozed: null };
  }
  for (const { teamId, status, count } of results) {
    if (!next[teamId]) next[teamId] = { open: null, pending: null, snoozed: null };
    next[teamId][status] = count;
  }
  teamCounts.value = next;
  loadingGrid.value = false;
};

const fetchLongestOpen = async () => {
  loadingLongest.value = true;
  try {
    // sort_by=last_activity_at_asc returns oldest-activity first, which is
    // exactly "this conversation has been sitting the longest". Page 1 is
    // enough — we only render LONGEST_OPEN_LIMIT rows.
    const { data } = await ConversationApi.get({
      status: 'open',
      sortBy: 'last_activity_at_asc',
      page: 1,
    });
    const payload =
      data?.data?.payload ?? data?.payload ?? data?.data ?? [];
    longestOpen.value = Array.isArray(payload)
      ? payload.slice(0, LONGEST_OPEN_LIMIT)
      : [];
  } catch (err) {
    longestOpen.value = [];
  }
  loadingLongest.value = false;
};

const refreshAll = async () => {
  errorMessage.value = '';
  try {
    await Promise.all([fetchHero(), fetchTeamGrid(), fetchLongestOpen()]);
  } catch (err) {
    errorMessage.value = err?.message || 'Failed to load some metrics.';
  }
};

// ── Lifecycle ─────────────────────────────────────────────────────────────
onMounted(async () => {
  // Teams may not be loaded yet — fetch them before we kick off the grid
  // call. The store action is idempotent so this is safe even on revisits.
  await store.dispatch('teams/get');
  refreshAll();
  refreshTimer = setInterval(refreshAll, REFRESH_INTERVAL_MS);
});

// The interval keeps firing if we don't clear it — Vue's unmount lifecycle
// doesn't reach into setInterval handles. Clear explicitly on unmount.
onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer);
});

// ── Drill-down navigation ─────────────────────────────────────────────────
// Every cell + hero card routes into Chatwoot's existing conversations list,
// pre-filtered. We use `team_conversations` for team rows (it natively
// scopes to one team) and fall back to `home` for the hero row, both with a
// ?status= query string for the status filter.
const goToTeamStatus = (teamId, status) => {
  router.push({
    name: 'team_conversations',
    params: { accountId: accountId.value, teamId },
    query: { status },
  });
};

const goToStatus = status => {
  router.push({
    name: 'home',
    params: { accountId: accountId.value },
    query: { status },
  });
};

const goToConversation = conv => {
  // Open the conversation in its primary inbox view. Chatwoot's home route
  // with conversation_id navigates correctly even when no inbox is selected.
  router.push({
    name: 'inbox_conversation',
    params: { accountId: accountId.value, conversation_id: conv.id },
  });
};

// ── Rendering helpers ─────────────────────────────────────────────────────
const fmtCount = v => (v === null || v === undefined ? '—' : String(v));

// Conversation last_activity_at is a unix epoch *number* in Chatwoot.
// formatDistanceToNowStrict wants a Date — convert at the boundary.
const waitingFor = conv => {
  const ts = conv?.last_activity_at;
  if (!ts) return '';
  try {
    return formatDistanceToNowStrict(new Date(Number(ts) * 1000), {
      addSuffix: false,
    });
  } catch {
    return '';
  }
};

const senderName = conv =>
  conv?.meta?.sender?.name || conv?.contact?.name || 'Unknown';

const channelLabel = conv => {
  const c = conv?.meta?.channel || conv?.inbox?.channel_type || '';
  // Chatwoot channel_type strings are namespaced: "Channel::FacebookPage" etc.
  return String(c).split('::').pop() || '—';
};

const inboxName = conv => conv?.meta?.inbox?.name || conv?.inbox?.name || '';

const teamName = teamId => {
  const t = teams.value.find(x => x.id === teamId);
  return t?.name || `Team #${teamId}`;
};

// Sum a row across the three statuses, ignoring nulls. Used as a per-team
// total to make the grid easier to scan.
const teamRowTotal = teamId => {
  const row = teamCounts.value[teamId];
  if (!row) return null;
  let sum = 0;
  let anyLoaded = false;
  for (const s of STATUSES) {
    const v = row[s];
    if (v !== null && v !== undefined) {
      sum += v;
      anyLoaded = true;
    }
  }
  return anyLoaded ? sum : null;
};

// Column total across all teams. Helps gut-check the totals.
const columnTotal = status => {
  let sum = 0;
  let anyLoaded = false;
  for (const team of teams.value) {
    const v = teamCounts.value[team.id]?.[status];
    if (v !== null && v !== undefined) {
      sum += v;
      anyLoaded = true;
    }
  }
  return anyLoaded ? sum : null;
};
</script>

<template>
  <ReportHeader :header-title="$t('OPERATIONS_REPORT.HEADER')" />

  <div class="flex flex-col gap-6 pb-6">
    <!-- Hero row: totals across the whole account, by status -->
    <section>
      <h3 class="text-sm font-medium text-n-slate-11 mb-3">
        {{ $t('OPERATIONS_REPORT.HERO_TITLE') }}
      </h3>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <button
          v-for="status in HERO_STATUSES"
          :key="status"
          type="button"
          class="flex flex-col items-start gap-1 p-4 rounded-lg bg-n-alpha-1 hover:bg-n-alpha-2 transition text-left"
          @click="goToStatus(status)"
        >
          <span class="text-xs text-n-slate-11 uppercase tracking-wider">
            {{ $t(`OPERATIONS_REPORT.STATUS.${status.toUpperCase()}`) }}
          </span>
          <span class="text-3xl font-semibold tabular-nums text-n-slate-12">
            {{ fmtCount(heroCounts[status]) }}
          </span>
        </button>
        <div
          class="flex flex-col items-start gap-1 p-4 rounded-lg bg-n-alpha-1"
        >
          <span class="text-xs text-n-slate-11 uppercase tracking-wider">
            {{ $t('OPERATIONS_REPORT.RESOLVED_TODAY') }}
          </span>
          <span class="text-3xl font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
            {{ fmtCount(resolvedTodayCount) }}
          </span>
        </div>
      </div>
    </section>

    <!-- Team grid: one row per team, columns per status. Cells clickable. -->
    <section>
      <h3 class="text-sm font-medium text-n-slate-11 mb-3">
        {{ $t('OPERATIONS_REPORT.TEAMS_TITLE') }}
      </h3>
      <div v-if="!teams.length" class="p-4 rounded-lg bg-n-alpha-1 text-sm text-n-slate-11">
        {{ $t('OPERATIONS_REPORT.NO_TEAMS') }}
      </div>
      <div v-else class="overflow-x-auto rounded-lg border border-n-weak">
        <table class="w-full text-sm">
          <thead>
            <tr class="bg-n-alpha-1 text-left text-xs uppercase tracking-wider text-n-slate-11">
              <th class="px-4 py-3 font-medium">
                {{ $t('OPERATIONS_REPORT.TEAM') }}
              </th>
              <th
                v-for="status in STATUSES"
                :key="status"
                class="px-4 py-3 font-medium text-right"
              >
                {{ $t(`OPERATIONS_REPORT.STATUS.${status.toUpperCase()}`) }}
              </th>
              <th class="px-4 py-3 font-medium text-right">
                {{ $t('OPERATIONS_REPORT.TOTAL') }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="team in teams"
              :key="team.id"
              class="border-t border-n-weak hover:bg-n-alpha-1 transition"
            >
              <td class="px-4 py-3 font-medium text-n-slate-12">
                {{ team.name }}
              </td>
              <td
                v-for="status in STATUSES"
                :key="status"
                class="px-4 py-3 text-right tabular-nums"
              >
                <button
                  type="button"
                  class="text-n-slate-12 hover:text-n-brand hover:underline disabled:opacity-50"
                  :disabled="!teamCounts[team.id] || teamCounts[team.id][status] === null"
                  @click="goToTeamStatus(team.id, status)"
                >
                  {{ fmtCount(teamCounts[team.id]?.[status]) }}
                </button>
              </td>
              <td class="px-4 py-3 text-right tabular-nums font-medium text-n-slate-12">
                {{ fmtCount(teamRowTotal(team.id)) }}
              </td>
            </tr>
            <!-- Footer: column totals -->
            <tr class="border-t border-n-weak bg-n-alpha-1">
              <td class="px-4 py-3 text-xs uppercase tracking-wider text-n-slate-11">
                {{ $t('OPERATIONS_REPORT.ALL_TEAMS') }}
              </td>
              <td
                v-for="status in STATUSES"
                :key="status"
                class="px-4 py-3 text-right tabular-nums font-medium text-n-slate-11"
              >
                {{ fmtCount(columnTotal(status)) }}
              </td>
              <td class="px-4 py-3" />
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Longest open conversations -->
    <section>
      <h3 class="text-sm font-medium text-n-slate-11 mb-3">
        {{ $t('OPERATIONS_REPORT.LONGEST_OPEN_TITLE') }}
      </h3>
      <div
        v-if="loadingLongest && !longestOpen.length"
        class="p-4 rounded-lg bg-n-alpha-1 text-sm text-n-slate-11"
      >
        {{ $t('OPERATIONS_REPORT.LOADING') }}
      </div>
      <div
        v-else-if="!longestOpen.length"
        class="p-4 rounded-lg bg-n-alpha-1 text-sm text-n-slate-11"
      >
        {{ $t('OPERATIONS_REPORT.NO_OPEN') }}
      </div>
      <div v-else class="overflow-x-auto rounded-lg border border-n-weak">
        <table class="w-full text-sm">
          <thead>
            <tr class="bg-n-alpha-1 text-left text-xs uppercase tracking-wider text-n-slate-11">
              <th class="px-4 py-3 font-medium">
                {{ $t('OPERATIONS_REPORT.CUSTOMER') }}
              </th>
              <th class="px-4 py-3 font-medium">
                {{ $t('OPERATIONS_REPORT.CHANNEL') }}
              </th>
              <th class="px-4 py-3 font-medium">
                {{ $t('OPERATIONS_REPORT.INBOX') }}
              </th>
              <th class="px-4 py-3 font-medium">
                {{ $t('OPERATIONS_REPORT.TEAM') }}
              </th>
              <th class="px-4 py-3 font-medium text-right">
                {{ $t('OPERATIONS_REPORT.WAITING') }}
              </th>
              <th class="px-4 py-3 font-medium text-right" />
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="conv in longestOpen"
              :key="conv.id"
              class="border-t border-n-weak hover:bg-n-alpha-1 transition"
            >
              <td class="px-4 py-3 font-medium text-n-slate-12 truncate max-w-[200px]">
                {{ senderName(conv) }}
              </td>
              <td class="px-4 py-3 text-n-slate-11">
                {{ channelLabel(conv) }}
              </td>
              <td class="px-4 py-3 text-n-slate-11 truncate max-w-[160px]">
                {{ inboxName(conv) }}
              </td>
              <td class="px-4 py-3 text-n-slate-11">
                {{ conv.meta?.team?.name || teamName(conv.team_id) || '—' }}
              </td>
              <td class="px-4 py-3 text-right tabular-nums text-amber-600 dark:text-amber-400">
                {{ waitingFor(conv) }}
              </td>
              <td class="px-4 py-3 text-right">
                <button
                  type="button"
                  class="text-xs font-medium text-n-brand hover:underline"
                  @click="goToConversation(conv)"
                >
                  {{ $t('OPERATIONS_REPORT.OPEN') }}
                  <span class="i-lucide-arrow-up-right align-middle" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <p v-if="errorMessage" class="text-xs text-ruby-600 dark:text-ruby-400 px-1">
      {{ errorMessage }}
    </p>
  </div>
</template>
