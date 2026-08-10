<script setup>
// Durian — conversation sidebar panel. Lists the active offers so an agent can
// push any of them (image + caption) to the customer on demand — the manual
// counterpart to the greeting auto-offer.
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useMapGetter } from 'dashboard/composables/store';
import { useAlert } from 'dashboard/composables';

const props = defineProps({
  conversationId: { type: [Number, String], required: true },
});

const { t } = useI18n();
const accountId = useMapGetter('getCurrentAccountId');
const axios = window.axios;

const offers = ref([]);
const sending = ref(null);

const load = async () => {
  try {
    const { data } = await axios.get(
      `/api/v1/accounts/${accountId.value}/offers`
    );
    offers.value = (data || []).filter(o => o.active && o.image_url);
  } catch {
    /* silent — the panel just shows empty */
  }
};
onMounted(load);

const send = async offer => {
  sending.value = offer.id;
  try {
    await axios.post(
      `/api/v1/accounts/${accountId.value}/offers/${offer.id}/send_to_conversation`,
      { conversation_id: props.conversationId }
    );
    useAlert(t('OFFERS_PANEL.SENT'));
  } catch {
    useAlert(t('OFFERS_PANEL.SEND_ERROR'));
  } finally {
    sending.value = null;
  }
};
</script>

<template>
  <div class="flex flex-col gap-3 px-4 py-3">
    <p v-if="!offers.length" class="text-sm text-n-slate-10">
      {{ $t('OFFERS_PANEL.EMPTY') }}
    </p>
    <div
      v-for="offer in offers"
      :key="offer.id"
      class="flex items-center gap-3"
    >
      <img
        :src="offer.image_url"
        class="object-cover rounded-md size-10 bg-n-alpha-1 shrink-0"
      />
      <span class="flex-1 min-w-0 text-sm truncate text-n-slate-12">
        {{ offer.caption }}
      </span>
      <button
        type="button"
        class="shrink-0 flex items-center gap-1 px-2.5 py-1 text-xs rounded-md outline-1 outline outline-n-container text-n-slate-11 hover:bg-n-alpha-2 disabled:opacity-50"
        :disabled="sending === offer.id"
        @click="send(offer)"
      >
        <span
          class="size-3"
          :class="
            sending === offer.id
              ? 'i-lucide-loader-2 animate-spin'
              : 'i-lucide-send'
          "
        />
        {{
          sending === offer.id
            ? $t('OFFERS_PANEL.SENDING')
            : $t('OFFERS_PANEL.SEND')
        }}
      </button>
    </div>
  </div>
</template>
