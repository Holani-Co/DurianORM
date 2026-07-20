<script setup>
// Phase 3 — the preview / dry-run tool. Paste a sample email and see how the
// LIVE rules would classify and route it (category, in-channel vs forward, and
// the forward target), without sending or saving anything. Backed by the
// bridge's /preview endpoint, which classifies against the current override
// merged onto the YAML — so it reflects exactly what production would do.
import { ref, computed } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  // The active override — passed to /preview so the dry-run reflects live config.
  override: { type: Object, default: () => ({}) },
});

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const subject = ref('');
const sender = ref('');
const body = ref('');
const busy = ref(false);
const result = ref(null);

const isForward = computed(() => result.value?.action === 'forward');

async function test() {
  if (busy.value) return;
  if (!body.value.trim()) {
    useAlert(t('ROUTING_CONFIG.PREVIEW.NEED_BODY'));
    return;
  }
  busy.value = true;
  result.value = null;
  try {
    const { data } = await axios.post(
      `/api/v1/accounts/${accountId.value}/integrations/routing_config/preview`,
      {
        doc: props.override || {},
        subject: subject.value,
        sender_email: sender.value,
        body: body.value,
      }
    );
    result.value = data;
  } catch (e) {
    useAlert(t('ROUTING_CONFIG.PREVIEW.ERROR'));
  } finally {
    busy.value = false;
  }
}

const confidencePct = computed(() =>
  result.value?.confidence != null
    ? `${Math.round(result.value.confidence * 100)}%`
    : '—'
);
</script>

<template>
  <div class="max-w-2xl">
    <p class="mb-4 text-sm text-n-slate-11">
      {{ t('ROUTING_CONFIG.PREVIEW.SUBTITLE') }}
    </p>

    <div class="flex flex-col gap-3">
      <label class="flex flex-col gap-1">
        <span class="text-xs font-medium text-n-slate-11">{{
          t('ROUTING_CONFIG.PREVIEW.SUBJECT')
        }}</span>
        <input
          v-model="subject"
          type="text"
          :placeholder="t('ROUTING_CONFIG.PREVIEW.SUBJECT_PH')"
          class="px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs font-medium text-n-slate-11">{{
          t('ROUTING_CONFIG.PREVIEW.SENDER')
        }}</span>
        <input
          v-model="sender"
          type="email"
          :placeholder="t('ROUTING_CONFIG.PREVIEW.SENDER_PH')"
          class="px-2.5 py-1.5 text-sm border rounded-lg outline-none border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs font-medium text-n-slate-11">{{
          t('ROUTING_CONFIG.PREVIEW.BODY')
        }}</span>
        <textarea
          v-model="body"
          rows="6"
          :placeholder="t('ROUTING_CONFIG.PREVIEW.BODY_PH')"
          class="px-2.5 py-1.5 text-sm border rounded-lg outline-none resize-y border-n-weak bg-n-surface text-n-slate-12 focus:border-n-brand"
        />
      </label>
      <div>
        <button
          type="button"
          class="px-4 py-1.5 text-sm font-medium rounded-lg text-white bg-n-brand hover:opacity-90 disabled:opacity-60"
          :disabled="busy"
          @click="test"
        >
          {{
            busy
              ? t('ROUTING_CONFIG.PREVIEW.TESTING')
              : t('ROUTING_CONFIG.PREVIEW.TEST')
          }}
        </button>
      </div>
    </div>

    <!-- result -->
    <div
      v-if="result"
      class="p-4 mt-5 border rounded-xl border-n-weak bg-n-surface"
    >
      <div
        class="mb-3 text-xs font-medium tracking-wide uppercase text-n-slate-10"
      >
        {{ t('ROUTING_CONFIG.PREVIEW.RESULT') }}
      </div>
      <div class="flex flex-wrap items-center gap-2 mb-3">
        <span class="text-base font-semibold text-n-slate-12">
          {{ result.display_name || result.category }}
        </span>
        <span
          class="px-2 py-0.5 text-xs font-medium rounded-full"
          :class="
            isForward
              ? 'bg-n-amber-2 text-n-amber-11'
              : 'bg-n-teal-3 text-n-teal-11'
          "
        >
          {{
            isForward
              ? t('ROUTING_CONFIG.PREVIEW.FORWARD')
              : t('ROUTING_CONFIG.PREVIEW.IN_CHANNEL')
          }}
        </span>
        <span class="text-xs text-n-slate-10">
          {{ t('ROUTING_CONFIG.PREVIEW.CONFIDENCE') }} {{ confidencePct }}
        </span>
      </div>

      <dl class="grid grid-cols-1 text-sm gap-y-2">
        <div v-if="isForward && result.forward_to" class="flex gap-2">
          <dt class="text-n-slate-10 min-w-[8rem]">
            {{ t('ROUTING_CONFIG.PREVIEW.FORWARD_TO') }}
          </dt>
          <dd class="text-n-slate-12">{{ result.forward_to }}</dd>
        </div>
        <div v-if="result.reason" class="flex gap-2">
          <dt class="text-n-slate-10 min-w-[8rem]">
            {{ t('ROUTING_CONFIG.PREVIEW.REASON') }}
          </dt>
          <dd class="text-n-slate-11">{{ result.reason }}</dd>
        </div>
        <div v-if="(result.alternatives || []).length" class="flex gap-2">
          <dt class="text-n-slate-10 min-w-[8rem]">
            {{ t('ROUTING_CONFIG.PREVIEW.ALTERNATIVES') }}
          </dt>
          <dd class="text-n-slate-11">
            {{ result.alternatives.map(a => a.category).join(', ') }}
          </dd>
        </div>
      </dl>
    </div>
  </div>
</template>
