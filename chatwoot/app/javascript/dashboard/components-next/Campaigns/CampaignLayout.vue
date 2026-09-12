<script setup>
import { vOnClickOutside } from '@vueuse/components';
import Button from 'dashboard/components-next/button/Button.vue';

defineProps({
  headerTitle: {
    type: String,
    default: '',
  },
  buttonLabel: {
    type: String,
    default: '',
  },
});

const emit = defineEmits(['click', 'close']);

const handleButtonClick = () => {
  emit('click');
};
</script>

<template>
  <section class="flex flex-col w-full h-full overflow-hidden bg-n-surface-1">
    <header class="sticky top-0 z-10 px-6">
      <div class="w-full max-w-5xl mx-auto">
        <div class="flex items-center justify-between w-full h-20 gap-2">
          <span class="text-heading-1 text-n-slate-12">
            {{ headerTitle }}
          </span>
          <div
            v-on-click-outside="[
              () => emit('close'),
              // Ignore clicks inside teleported overlays that are not DOM children
              // of this button: the editor Create-link popup and the campaign/
              // template/consent modals (CampaignModal) — otherwise a click inside
              // the modal counts as 'outside' and closes it.
              {
                ignore: [
                  'dialog.ProseMirror-prompt-backdrop',
                  '.campaign-modal-overlay',
                ],
              },
            ]"
            class="relative group/campaign-button"
          >
            <Button
              :label="buttonLabel"
              icon="i-lucide-plus"
              size="sm"
              class="group-hover/campaign-button:brightness-110"
              @click="handleButtonClick"
            />
            <slot name="action" />
          </div>
        </div>
      </div>
    </header>
    <main class="flex-1 px-6 overflow-y-auto">
      <div class="w-full max-w-5xl mx-auto py-4">
        <slot name="default" />
      </div>
    </main>
  </section>
</template>
