<script setup>
import { onMounted, onBeforeUnmount } from 'vue';
import Button from 'dashboard/components-next/button/Button.vue';

defineProps({
  widthClass: {
    type: String,
    default: 'max-w-2xl',
  },
});

const emit = defineEmits(['close']);

const close = () => emit('close');

// Escape closes the modal, matching the site's Dialog behaviour.
const handleKeydown = event => {
  if (event.key === 'Escape') close();
};

onMounted(() => document.addEventListener('keydown', handleKeydown));
onBeforeUnmount(() => document.removeEventListener('keydown', handleKeydown));
</script>

<template>
  <Teleport to="body">
    <div
      class="campaign-modal-overlay fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-n-alpha-black1 p-4 backdrop-blur-[4px]"
      @click.self="close"
    >
      <div
        class="relative my-8 w-full rounded-xl border border-n-weak bg-n-alpha-3 shadow-xl backdrop-blur-[100px]"
        :class="widthClass"
      >
        <Button
          type="button"
          size="sm"
          variant="ghost"
          color="slate"
          icon="i-lucide-x"
          class="absolute top-3 ltr:right-3 rtl:left-3 z-10"
          @click="close"
        />
        <div class="max-h-[calc(100vh-6rem)] overflow-y-auto">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>
