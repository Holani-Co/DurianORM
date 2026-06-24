<script setup>
// Durian — guided short_code builder for the Add Canned Response form.
// Pick a channel + category (or type a new category) and the short_code is
// assembled to the `<channel>_<category>` convention, so the team never has to
// type/remember the format. Emits the assembled code via v-model; the parent's
// short_code field stays editable for power users.
import { ref, computed, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  TEMPLATE_CHANNELS,
  buildShortCode,
  channelMeta,
} from 'dashboard/helper/templateTaxonomy';

const props = defineProps({
  existingShortCodes: { type: Array, default: () => [] },
});

// The assembled short_code is pushed up via v-model on the parent.
const model = defineModel({ type: String, default: '' });

const { t } = useI18n();

const channel = ref(TEMPLATE_CHANNELS[0].id);
const category = ref('');

const knownCategories = computed(() => channelMeta(channel.value).categories);

const assembled = computed(() => buildShortCode(channel.value, category.value));

const isDuplicate = computed(
  () => !!category.value && props.existingShortCodes.includes(assembled.value)
);

// Push the assembled code up whenever channel/category changes.
watch(assembled, val => {
  model.value = val;
});
</script>

<template>
  <div class="flex flex-col gap-3 p-3 mb-2 rounded-lg bg-n-solid-2">
    <span class="text-sm font-medium text-n-slate-12">
      {{ t('CANNED_MGMT.NAME_BUILDER.TITLE') }}
    </span>
    <div class="flex flex-col gap-3 sm:flex-row">
      <label class="flex flex-col flex-1 gap-1">
        <span class="text-xs text-n-slate-11">
          {{ t('CANNED_MGMT.NAME_BUILDER.CHANNEL_LABEL') }}
        </span>
        <select
          v-model="channel"
          class="px-2 py-1.5 text-sm rounded-md bg-n-background text-n-slate-12 border border-n-weak"
        >
          <option v-for="c in TEMPLATE_CHANNELS" :key="c.id" :value="c.id">
            {{ c.label }}
          </option>
        </select>
      </label>
      <label class="flex flex-col flex-1 gap-1">
        <span class="text-xs text-n-slate-11">
          {{ t('CANNED_MGMT.NAME_BUILDER.CATEGORY_LABEL') }}
        </span>
        <input
          v-model="category"
          list="template-category-options"
          :placeholder="t('CANNED_MGMT.NAME_BUILDER.CATEGORY_PLACEHOLDER')"
          class="px-2 py-1.5 text-sm rounded-md bg-n-background text-n-slate-12 border border-n-weak"
        />
        <datalist id="template-category-options">
          <option
            v-for="cat in knownCategories"
            :key="cat.value"
            :value="cat.value"
          >
            {{ cat.label }}
          </option>
        </datalist>
      </label>
    </div>
    <div class="flex items-center gap-2">
      <span class="i-ph-arrow-right text-n-slate-10" />
      <code class="px-2 py-0.5 text-sm rounded bg-n-solid-3 text-n-slate-12">
        {{ assembled }}
      </code>
    </div>
    <span v-if="isDuplicate" class="text-xs text-n-ruby-11">
      {{ t('CANNED_MGMT.NAME_BUILDER.DUPLICATE_WARNING') }}
    </span>
  </div>
</template>
