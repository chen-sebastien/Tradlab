<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{values:number[],secondary?:number[],labels?:string[],height?:number}>()
const all=computed(()=>[...props.values,...(props.secondary||[])].filter(Number.isFinite))
const lo=computed(()=>all.value.length?Math.min(...all.value)*.995:0)
const hi=computed(()=>all.value.length?Math.max(...all.value)*1.005:1)
const path=(values:number[])=>values.map((v,i)=>`${i?'L':'M'}${40+i*700/Math.max(1,values.length-1)},${160-(v-lo.value)/(hi.value-lo.value||1)*135}`).join(' ')
</script>
<template><div class="chart"><svg v-if="values.length" viewBox="0 0 780 200" role="img" aria-label="價格或淨值歷史曲線">
<defs><linearGradient id="shade" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#3f8f76" stop-opacity=".2"/><stop offset="1" stop-color="#3f8f76" stop-opacity="0"/></linearGradient></defs>
<g v-for="i in 4" :key="i"><line x1="40" x2="740" :y1="i*40" :y2="i*40" stroke="#e4e9e4" stroke-dasharray="3 5"/><text x="2" :y="i*40-5" fill="#7f8d87" font-size="9">{{(hi-(hi-lo)*(i*40-25)/135).toFixed(0)}}</text></g>
<path :d="path(values)+' L740,165 L40,165 Z'" fill="url(#shade)"/><path v-if="secondary?.length" :d="path(secondary)" fill="none" stroke="#a7aa9c" stroke-width="2" stroke-dasharray="5 4"/>
<path :d="path(values)" fill="none" stroke="#21695b" stroke-width="2.4"/>
<text x="40" y="190" fill="#7f8d87" font-size="11">{{labels?.[0]}}</text><text x="740" y="190" text-anchor="end" fill="#7f8d87" font-size="11">{{labels?.[labels.length-1]}}</text></svg><div v-else class="empty">尚無可顯示的曲線</div></div></template>
