import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { DATA_HEALTH_INVALIDATE_PREFIXES, QK } from './queryKeys'

export function useDataHealthJobs() {
  const qc = useQueryClient()
  const seen = useRef(new Set<string>())
  const jobs = useQuery({
    queryKey: QK.dataHealthJobs, queryFn: api.dataHealthJobs,
    refetchInterval: query => query.state.data?.some(j => ['queued', 'running'].includes(j.status)) ? 1500 : 10_000,
  })
  useEffect(() => {
    for (const job of jobs.data ?? []) {
      if (['queued', 'running'].includes(job.status) || seen.current.has(job.job_id)) continue
      seen.current.add(job.job_id)
      if (seen.current.size > 60) seen.current.delete(seen.current.values().next().value!)
      const group = job.action === 'validate' ? 'validate' : job.affected_datasets.includes('daily') ? 'daily' : 'social'
      void qc.invalidateQueries({ predicate: query => DATA_HEALTH_INVALIDATE_PREFIXES[group].some(prefix =>
        String(query.queryKey[0]).startsWith(prefix)) })
    }
  }, [jobs.data, qc])
  return jobs
}

export function useDataHealth() {
  return useQuery({
    queryKey: QK.dataHealth,
    queryFn: api.dataHealth,
    staleTime: 10_000,
    refetchInterval: 10_000,
  })
}
