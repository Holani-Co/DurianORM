<script setup>
// Product lines: which deals each line takes and which WhatsApp number speaks for
// it. A line's rules can only use templates approved on that number.
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import FilterRows from './FilterRows.vue';

defineProps({ ops: { type: Array, default: () => [] } });
const lines = defineModel('lines', { type: Array, required: true });

const { t } = useI18n();
const inboxes = useMapGetter('inboxes/getWhatsAppInboxes');

const input =
  'px-2 py-1.5 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12';
</script>

<template>
  <section class="p-4 border rounded-xl border-n-weak">
    <h2 class="text-sm font-medium text-n-slate-12">
      {{ t('CRM_AUTOMATION_CONFIG.LINES.TITLE') }}
    </h2>
    <p class="mt-1 text-xs text-n-slate-11">
      {{ t('CRM_AUTOMATION_CONFIG.LINES.HINT') }}
    </p>
    <div
      v-for="line in lines"
      :key="line.id"
      class="pt-3 mt-3 border-t border-n-weak"
    >
      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-2 text-sm text-n-slate-12">
          <input v-model="line.enabled" type="checkbox" />
          {{ t('CRM_AUTOMATION_CONFIG.RULES.ENABLED') }}
        </label>
        <input
          v-model="line.name"
          type="text"
          class="flex-1 min-w-[12rem] font-medium"
          :class="input"
        />
        <label class="flex items-center gap-2 text-xs text-n-slate-11">
          {{ t('CRM_AUTOMATION_CONFIG.LINES.INBOX') }}
          <select v-model.number="line.inbox_id" :class="input">
            <option v-for="ib in inboxes" :key="ib.id" :value="ib.id">
              {{ ib.name }}
            </option>
          </select>
        </label>
      </div>
      <div class="mt-2 text-xs text-n-slate-11">
        {{ t('CRM_AUTOMATION_CONFIG.LINES.FILTERS') }}
      </div>
      <FilterRows v-model:match="line.match" :ops="ops" :input-class="input" />
    </div>
  </section>
</template>
