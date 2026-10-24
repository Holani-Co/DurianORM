<script setup>
// Preview what the published rules would do for a real deal (nothing sent),
// and send one rule's message to a test number.
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

defineProps({ rules: { type: Array, default: () => [] } });

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;
const base = () =>
  `/api/v1/accounts/${accountId.value}/integrations/crm_automation_config`;

const dealId = ref('');
const preview = ref(null);
const ruleId = ref('');
const phone = ref('');
const busy = ref('');
const input =
  'px-3 py-2 text-sm border rounded-lg border-n-weak bg-n-alpha-black-2 text-n-slate-12';
const errorText = e =>
  e?.response?.data?.detail?.message || t('CRM_AUTOMATION_CONFIG.TEST.FAILED');

async function runPreview() {
  busy.value = 'preview';
  preview.value = null;
  try {
    const { data } = await axios.post(`${base()}/preview`, {
      deal_id: dealId.value.trim(),
    });
    preview.value = data;
  } catch (e) {
    useAlert(errorText(e));
  } finally {
    busy.value = '';
  }
}

async function sendTest() {
  busy.value = 'send';
  try {
    const { data } = await axios.post(`${base()}/test_send`, {
      rule_id: ruleId.value,
      deal_id: dealId.value.trim(),
      phone: phone.value.trim(),
    });
    useAlert(
      data.status === 'sent'
        ? t('CRM_AUTOMATION_CONFIG.TEST.SENT')
        : t('CRM_AUTOMATION_CONFIG.TEST.NOT_SENT', { detail: data.detail })
    );
  } catch (e) {
    useAlert(errorText(e));
  } finally {
    busy.value = '';
  }
}

const yes = v =>
  v ? t('CRM_AUTOMATION_CONFIG.TEST.YES') : t('CRM_AUTOMATION_CONFIG.TEST.NO');
</script>

<template>
  <div class="flex flex-col gap-6 max-w-4xl">
    <section class="p-4 border rounded-xl border-n-weak">
      <h2 class="text-base font-medium text-n-slate-12">
        {{ t('CRM_AUTOMATION_CONFIG.TEST.PREVIEW_TITLE') }}
      </h2>
      <p class="mt-1 text-xs text-n-slate-11">
        {{ t('CRM_AUTOMATION_CONFIG.TEST.PREVIEW_HINT') }}
      </p>
      <div class="flex flex-wrap gap-2 mt-3">
        <input
          v-model="dealId"
          type="text"
          inputmode="numeric"
          :placeholder="t('CRM_AUTOMATION_CONFIG.TEST.DEAL_ID')"
          class="w-72"
          :class="input"
        />
        <button
          type="button"
          class="px-3 py-2 text-sm font-medium border rounded-lg border-n-weak text-n-brand hover:bg-n-alpha-1 disabled:opacity-60"
          :disabled="!dealId.trim() || busy !== ''"
          @click="runPreview"
        >
          {{ t('CRM_AUTOMATION_CONFIG.TEST.PREVIEW') }}
        </button>
      </div>

      <div v-if="preview" class="mt-3">
        <p class="text-xs text-n-slate-12">
          {{
            t('CRM_AUTOMATION_CONFIG.TEST.DEAL_INFO', {
              name: preview.deal.name,
              stage: preview.deal.stage,
              owner: preview.deal.owner || '—',
              phone: preview.deal.phone ? `+${preview.deal.phone}` : '—',
            })
          }}
        </p>
        <div class="mt-2 overflow-x-auto border rounded-xl border-n-weak">
          <table class="min-w-full text-xs">
            <thead class="bg-n-alpha-1 text-n-slate-11">
              <tr>
                <th class="px-3 py-2 text-left">{{ t('CRM_AUTOMATION_CONFIG.TEST.COL_RULE') }}</th>
                <th class="px-3 py-2 text-left">{{ t('CRM_AUTOMATION_CONFIG.TEST.COL_STAGE') }}</th>
                <th class="px-3 py-2 text-left">{{ t('CRM_AUTOMATION_CONFIG.TEST.COL_FILTER') }}</th>
                <th class="px-3 py-2 text-left">{{ t('CRM_AUTOMATION_CONFIG.TEST.COL_TEMPLATE') }}</th>
                <th class="px-3 py-2 text-left">{{ t('CRM_AUTOMATION_CONFIG.TEST.COL_MESSAGE') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="r in preview.rules"
                :key="r.rule"
                class="align-top border-t border-n-weak text-n-slate-12"
              >
                <td class="px-3 py-2">
                  {{ r.name || r.rule }}
                  <span v-if="!r.enabled" class="text-n-slate-10">(off)</span>
                </td>
                <td class="px-3 py-2">{{ yes(r.stage_matches) }}</td>
                <td class="px-3 py-2">
                  {{ yes(r.filters_match) }}
                  <div v-if="r.why_not" class="text-n-slate-10">{{ r.why_not }}</div>
                </td>
                <td class="px-3 py-2">{{ yes(r.template_found) }}</td>
                <td class="px-3 py-2 whitespace-pre-wrap">{{ r.message || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="p-4 border rounded-xl border-n-weak">
      <h2 class="text-base font-medium text-n-slate-12">
        {{ t('CRM_AUTOMATION_CONFIG.TEST.SEND_TITLE') }}
      </h2>
      <p class="mt-1 text-xs text-n-slate-11">
        {{ t('CRM_AUTOMATION_CONFIG.TEST.SEND_HINT') }}
      </p>
      <div class="flex flex-wrap gap-2 mt-3">
        <select v-model="ruleId" :class="input">
          <option value="" disabled>{{ t('CRM_AUTOMATION_CONFIG.TEST.RULE') }}</option>
          <option v-for="r in rules" :key="r.id" :value="r.id">
            {{ r.name || r.id }}
          </option>
        </select>
        <input
          v-model="phone"
          type="tel"
          :placeholder="t('CRM_AUTOMATION_CONFIG.TEST.PHONE')"
          class="w-60"
          :class="input"
        />
        <button
          type="button"
          class="px-3 py-2 text-sm font-medium text-white rounded-lg bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="!ruleId || !dealId.trim() || !phone.trim() || busy !== ''"
          @click="sendTest"
        >
          {{ t('CRM_AUTOMATION_CONFIG.TEST.SEND') }}
        </button>
      </div>
    </section>
  </div>
</template>
