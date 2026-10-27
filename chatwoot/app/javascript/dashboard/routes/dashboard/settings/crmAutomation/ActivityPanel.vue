<script setup>
// What the automation did, newest first (sent / scheduled / skipped / …).
import { useI18n } from 'vue-i18n';

defineProps({ runs: { type: Array, default: () => [] } });
const { t } = useI18n();

const formatWhen = iso => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};
const tone = status =>
  ({
    sent: 'bg-n-teal-3 text-n-teal-11',
    scheduled: 'bg-n-blue-3 text-n-blue-11',
    failed: 'bg-n-ruby-3 text-n-ruby-11',
  })[status] || 'bg-n-alpha-2 text-n-slate-11';
</script>

<template>
  <div>
    <p v-if="!runs.length" class="text-sm text-n-slate-11">
      {{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.EMPTY') }}
    </p>
    <div v-else class="overflow-x-auto border rounded-xl border-n-weak">
      <table class="min-w-full text-sm">
        <thead class="bg-n-alpha-1 text-n-slate-11">
          <tr>
            <th class="px-3 py-2 font-medium text-left">{{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.WHEN') }}</th>
            <th class="px-3 py-2 font-medium text-left">{{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.DEAL') }}</th>
            <th class="px-3 py-2 font-medium text-left">{{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.RULE') }}</th>
            <th class="px-3 py-2 font-medium text-left">{{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.STATUS') }}</th>
            <th class="px-3 py-2 font-medium text-left">{{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.PHONE') }}</th>
            <th class="px-3 py-2 font-medium text-left">{{ t('CRM_AUTOMATION_CONFIG.ACTIVITY.DETAIL') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in runs"
            :key="r.id"
            class="border-t border-n-weak text-n-slate-12"
          >
            <td class="px-3 py-2 text-xs whitespace-nowrap">{{ formatWhen(r.updated_at) }}</td>
            <td class="px-3 py-2 font-mono text-xs">{{ r.deal_id }}</td>
            <td class="px-3 py-2 text-xs">{{ r.rule_id }}</td>
            <td class="px-3 py-2">
              <span class="px-2 py-0.5 text-xs rounded-full" :class="tone(r.status)">
                {{ t(`CRM_AUTOMATION_CONFIG.ACTIVITY.STATUSES.${r.status}`) }}
              </span>
            </td>
            <td class="px-3 py-2 font-mono text-xs">{{ r.phone ? `+${r.phone}` : '—' }}</td>
            <td class="px-3 py-2 text-xs text-n-slate-11">
              {{ r.status === 'scheduled' ? formatWhen(r.due_at) : r.detail || '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
