<script setup>
// Zoho owner sync. Lists deal owners (routing config + Settings → Stores) whose
// Zoho CRM user is deactivated or deleted, and who gets those deals meanwhile
// (a same-named successor or the reporting manager; else the fallback owner).
// The bridge refreshes Zoho's users daily; "Sync with Zoho" re-reads them now.
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const report = ref(null);
const busy = ref(false);
const failed = ref(false);

const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/routing_config`;

async function load(run = false) {
  busy.value = true;
  failed.value = false;
  try {
    const { data } = run
      ? await axios.post(`${base()}/owner_sync_run`)
      : await axios.get(`${base()}/owner_sync`);
    report.value = data;
    failed.value = Boolean(data?.error);
  } catch (e) {
    failed.value = true;
  } finally {
    busy.value = false;
  }
}

onMounted(() => load());

const statusLabel = s =>
  s === 'deleted'
    ? t('ROUTING_CONFIG.OWNER_SYNC.STATUS.deleted')
    : t('ROUTING_CONFIG.OWNER_SYNC.STATUS.deactivated');

const successorText = s => `${s.name} · ${s.email} (${s.reason})`;

const where = p =>
  p.kind === 'store'
    ? t('ROUTING_CONFIG.OWNER_SYNC.STORE', { name: p.label })
    : `${p.group} · ${p.label}`;
</script>

<template>
  <div class="p-3 mb-5 border rounded-xl border-n-weak bg-n-alpha-1">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <div class="text-sm font-medium text-n-slate-12">
          {{ t('ROUTING_CONFIG.OWNER_SYNC.TITLE') }}
        </div>
        <div class="text-xs text-n-slate-10">
          <span v-if="report?.synced_at">
            {{
              t('ROUTING_CONFIG.OWNER_SYNC.SYNCED', {
                when: report.synced_at,
                count: report.active_users,
              })
            }}
          </span>
          <span v-else>{{ t('ROUTING_CONFIG.OWNER_SYNC.NEVER') }}</span>
        </div>
      </div>
      <button
        type="button"
        class="px-3 py-1.5 text-sm font-medium border rounded-lg border-n-weak text-n-slate-12 hover:bg-n-alpha-2 disabled:opacity-50"
        :disabled="busy"
        @click="load(true)"
      >
        {{
          busy
            ? t('ROUTING_CONFIG.OWNER_SYNC.SYNCING')
            : t('ROUTING_CONFIG.OWNER_SYNC.SYNC_NOW')
        }}
      </button>
    </div>

    <p v-if="failed" class="mt-2 text-xs text-n-ruby-11">
      {{ t('ROUTING_CONFIG.OWNER_SYNC.FAILED') }}
    </p>
    <p
      v-else-if="report?.ready && !report.problems.length"
      class="mt-2 text-xs text-n-teal-11"
    >
      {{ t('ROUTING_CONFIG.OWNER_SYNC.ALL_ACTIVE') }}
    </p>

    <div v-if="report?.problems?.length" class="mt-3">
      <p class="mb-2 text-xs text-n-slate-11">
        {{ t('ROUTING_CONFIG.OWNER_SYNC.HINT') }}
      </p>
      <table class="w-full text-xs text-left">
        <thead class="text-n-slate-10">
          <tr>
            <th class="py-1 pr-2 font-medium">
              {{ t('ROUTING_CONFIG.OWNER_SYNC.COL_WHERE') }}
            </th>
            <th class="py-1 pr-2 font-medium">
              {{ t('ROUTING_CONFIG.OWNER_SYNC.COL_OWNER') }}
            </th>
            <th class="py-1 pr-2 font-medium">
              {{ t('ROUTING_CONFIG.OWNER_SYNC.COL_NOW') }}
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="p in report.problems"
            :key="`${p.kind}|${p.group}|${p.label}`"
            class="border-t border-n-weak"
          >
            <td class="py-1.5 pr-2 text-n-slate-12">{{ where(p) }}</td>
            <td class="py-1.5 pr-2 text-n-slate-11">
              {{ p.owner_name || p.owner_email || p.owner_id }}
              <span
                class="ml-1 px-1.5 py-0.5 rounded-full bg-n-ruby-3 text-n-ruby-11"
              >
                {{ statusLabel(p.status) }}
              </span>
            </td>
            <td class="py-1.5 pr-2 text-n-slate-11">
              <span v-if="p.successor">{{ successorText(p.successor) }}</span>
              <span v-else class="text-n-amber-11">
                {{ t('ROUTING_CONFIG.OWNER_SYNC.FALLBACK') }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
