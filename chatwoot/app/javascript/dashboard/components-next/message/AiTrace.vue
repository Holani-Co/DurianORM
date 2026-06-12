<script setup>
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';

/**
 * Renders the bot's chain-of-thought under a reply: an ordered, collapsible
 * list of the steps it took, each tagged with WHY it happened —
 *   source 'system' → a deterministic code decision
 *   source 'rule'   → a rule from the prompt (e.g. a handoff/redirect)
 *   source 'model'  → the model's own reasoning or a tool it chose to call
 * Steps are produced by Integrations::DmBot::ProcessorService and stored on
 * message.content_attributes.ai_trace.
 */
defineProps({
  steps: {
    type: Array,
    default: () => [],
  },
});

const { t } = useI18n();

const isOpen = ref(false);

const SOURCE_ICONS = {
  system: 'i-lucide-cog',
  rule: 'i-lucide-book-text',
  model: 'i-lucide-sparkles',
};

const iconForSource = source => SOURCE_ICONS[source] || SOURCE_ICONS.model;

// Rules arrive as prompt enums like `price_question` — display as words.
const humanizeRule = rule => (rule || '').replaceAll('_', ' ');
</script>

<template>
  <div
    class="flex flex-col w-full max-w-md overflow-hidden border rounded-lg border-n-weak bg-n-alpha-1"
  >
    <button
      type="button"
      class="flex items-center w-full gap-2 px-3 py-2 text-left"
      @click="isOpen = !isOpen"
    >
      <span class="text-base i-lucide-sparkles text-n-slate-11 shrink-0" />
      <span class="flex-1 text-sm font-medium text-n-slate-12">
        {{ t('CONVERSATION.AI_TRACE.TITLE') }}
      </span>
      <span class="text-xs tabular-nums text-n-slate-10">{{
        steps.length
      }}</span>
      <span
        class="text-base shrink-0 text-n-slate-10"
        :class="isOpen ? 'i-lucide-chevron-up' : 'i-lucide-chevron-down'"
      />
    </button>
    <ol v-if="isOpen" class="flex flex-col gap-3 px-3 pt-1 pb-3">
      <li v-for="step in steps" :key="step.i" class="flex items-start gap-2">
        <span
          class="text-base i-lucide-circle-check text-n-teal-10 shrink-0 mt-0.5"
        />
        <div class="flex flex-col min-w-0 gap-0.5">
          <div class="flex flex-wrap items-center gap-1.5">
            <span class="text-sm font-medium text-n-slate-12">
              {{ step.label }}
            </span>
            <span
              v-if="step.rule"
              class="text-xs font-medium px-1 py-px rounded bg-n-alpha-2 text-n-slate-11"
            >
              {{ humanizeRule(step.rule) }}
            </span>
            <span
              v-if="step.input"
              class="text-xs px-1 py-px rounded bg-n-alpha-2 text-n-slate-11 truncate max-w-48"
            >
              {{ step.input }}
            </span>
            <span
              v-tooltip.top="step.source"
              class="text-xs shrink-0 text-n-slate-10"
              :class="iconForSource(step.source)"
            />
          </div>
          <p v-if="step.detail" class="text-xs leading-snug text-n-slate-11">
            {{ step.detail }}
          </p>
        </div>
      </li>
    </ol>
  </div>
</template>
