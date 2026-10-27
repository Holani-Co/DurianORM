<script setup>
// Every published version of the rules + one-click restore (audited).
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const emit = defineEmits(['restored']);
const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;
const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/crm_automation_config`;

const loading = ref(true);
const error = ref(false);
const versions = ref([]);
const confirmingId = ref(null);
const busy = ref(false);

const formatWhen = iso => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

async function load() {
  loading.value = true;
  error.value = false;
  try {
    const { data } = await axios.get(`${base()}/versions`);
    versions.value = data.versions || [];
  } catch (e) {
    error.value = true;
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function restore(id) {
  busy.value = true;
  try {
    await axios.post(`${base()}/rollback`, { version_id: id });
    useAlert(t('CRM_AUTOMATION_CONFIG.HISTORY.RESTORED'));
    confirmingId.value = null;
    await load();
    emit('restored');
  } catch (e) {
    useAlert(t('CRM_AUTOMATION_CONFIG.HISTORY.RESTORE_FAILED'));
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="max-w-3xl">
    <p class="mb-4 text-sm text-n-slate-11">{{ t('CRM_AUTOMATION_CONFIG.HISTORY.SUBTITLE') }}</p>
    <p v-if="loading" class="text-sm text-n-slate-11">{{ t('CRM_AUTOMATION_CONFIG.HISTORY.LOADING') }}</p>
    <p v-else-if="error" class="text-sm text-n-ruby-11">{{ t('CRM_AUTOMATION_CONFIG.HISTORY.ERROR') }}</p>
    <p v-else-if="!versions.length" class="text-sm text-n-slate-11">{{ t('CRM_AUTOMATION_CONFIG.HISTORY.EMPTY') }}</p>
    <div v-else class="flex flex-col gap-2">
      <div
        v-for="v in versions"
        :key="v.id"
        class="flex flex-wrap items-center gap-2 px-4 py-3 border rounded-xl border-n-weak"
        :class="v.active ? 'border-n-brand' : ''"
      >
        <span class="font-medium text-n-slate-12">
          {{ t('CRM_AUTOMATION_CONFIG.HISTORY.VERSION') }} {{ v.id }}
        </span>
        <span
          v-if="v.active"
          class="px-2 py-0.5 text-xs font-medium rounded-full bg-n-teal-3 text-n-teal-11"
        >
          {{ t('CRM_AUTOMATION_CONFIG.HISTORY.ACTIVE') }}
        </span>
        <span v-if="v.note" class="text-sm text-n-slate-11">{{ v.note }}</span>
        <span class="text-xs text-n-slate-10">
          {{ t('CRM_AUTOMATION_CONFIG.HISTORY.BY') }} {{ v.created_by || '—' }} · {{ formatWhen(v.created_at) }}
        </span>
        <span v-if="!v.active" class="flex gap-2 ml-auto">
          <template v-if="confirmingId === v.id">
            <button
              type="button"
              class="px-3 py-1 text-xs font-medium text-white rounded-lg bg-n-ruby-11 disabled:opacity-60"
              :disabled="busy"
              @click="restore(v.id)"
            >
              {{ t('CRM_AUTOMATION_CONFIG.HISTORY.CONFIRM_RESTORE') }}
            </button>
            <button type="button" class="px-2 py-1 text-xs text-n-slate-11" @click="confirmingId = null">
              {{ t('CRM_AUTOMATION_CONFIG.HISTORY.CANCEL') }}
            </button>
          </template>
          <button
            v-else
            type="button"
            class="px-3 py-1 text-xs font-medium border rounded-lg border-n-weak text-n-brand"
            @click="confirmingId = v.id"
          >
            {{ t('CRM_AUTOMATION_CONFIG.HISTORY.RESTORE') }}
          </button>
        </span>
      </div>
    </div>
  </div>
</template>
