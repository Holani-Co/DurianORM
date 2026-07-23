<script setup>
import { ref } from 'vue';
import FilterListDropdown from 'dashboard/components/ui/Dropdown/DropdownList.vue';

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] }, // array of { id, name }
  placeholder: { type: String, default: 'Select...' },
  hasEdit: { type: Boolean, default: false }
});

const emit = defineEmits(['update:modelValue', 'change']);

const open = ref(false);

function toggle() {
  open.value = !open.value;
}

function close() {
  open.value = false;
}

function onSelect(item) {
  emit('update:modelValue', item.id);
  emit('change', item.id);
  close();
}
</script>

<template>
  <div class="relative w-full">
    <button
      type="button"
      class="flex items-center justify-between w-full px-2.5 py-1.5 text-sm text-left border rounded-lg outline-none bg-n-surface text-n-slate-12 focus:border-n-brand transition-colors"
      :class="[hasEdit ? 'border-n-brand' : 'border-n-weak', open ? 'ring-1 ring-n-brand border-n-brand' : '']"
      @click="toggle"
    >
      <span class="truncate">{{ modelValue || placeholder }}</span>
      <span class="i-lucide-chevron-down shrink-0 w-4 h-4 ml-2 text-n-slate-10" />
    </button>
    <FilterListDropdown
      v-if="open"
      v-on-clickaway="close"
      :show-clear-filter="false"
      :list-items="options"
      :active-filter-id="modelValue"
      :input-placeholder="placeholder"
      enable-search
      class="absolute left-0 right-0 flex flex-col w-full h-fit !max-h-[250px] top-full mt-1 z-[100] shadow-2xl !border !border-n-strong overflow-y-auto !bg-n-surface"
      @select="onSelect"
    />
  </div>
</template>
