import { useQuery } from '@tanstack/react-query';
import { api } from './client';
import type { MarketsResponse } from '../types';

export function useMarkets() {
  return useQuery({
    queryKey: ['markets'],
    queryFn: async () => {
      const { data } = await api.get<MarketsResponse>('/markets');
      return data.markets;
    },
    refetchInterval: 60000,  // Refresh every minute
  });
}
