/*---------------------------------------------------------------------------------------------
 *  Copyright (c) Microsoft Corporation. All rights reserved.
 *  Licensed under the MIT License. See LICENSE in the project root for license information.
 *--------------------------------------------------------------------------------------------*/

import { useCallback, useEffect, useState } from 'react'
import { api } from '../services/api'
import { customScenarioService } from '../services/customScenarios'
import { CustomScenario, CustomScenarioData, Scenario } from '../types'

export function useScenarios() {
  const [serverScenarios, setServerScenarios] = useState<Scenario[]>([])
  // Initialize custom scenarios from localStorage synchronously
  const [customScenarios, setCustomScenarios] = useState<CustomScenario[]>(() =>
    customScenarioService.getAll()
  )
  const [selectedScenario, setSelectedScenario] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Load scenarios on mount
  useEffect(() => {
    // Load server scenarios
    api
      .getScenarios()
      .then(setServerScenarios)
      .finally(() => setLoading(false))
  }, [])

  // Combined scenarios list
  const scenarios: Scenario[] = [...serverScenarios, ...customScenarios]

  // Get a specific custom scenario by ID
  const getCustomScenario = useCallback(
    (id: string): CustomScenario | null => {
      return customScenarios.find(s => s.id === id) || null
    },
    [customScenarios]
  )

  // Add a new custom scenario
  const addCustomScenario = useCallback(
    (
      name: string,
      description: string,
      scenarioData: CustomScenarioData
    ): CustomScenario => {
      const newScenario = customScenarioService.save(
        name,
        description,
        scenarioData
      )
      setCustomScenarios(prev => [...prev, newScenario])
      return newScenario
    },
    []
  )

  // Update a custom scenario
  const updateCustomScenario = useCallback(
    (
      id: string,
      updates: Partial<
        Pick<CustomScenario, 'name' | 'description' | 'scenarioData'>
      >
    ): CustomScenario | null => {
      const updated = customScenarioService.update(id, updates)
      if (updated) {
        setCustomScenarios(prev => prev.map(s => (s.id === id ? updated : s)))
      }
      return updated
    },
    []
  )

  // Delete a custom scenario
  const deleteCustomScenario = useCallback(
    (id: string): boolean => {
      const deleted = customScenarioService.delete(id)
      if (deleted) {
        setCustomScenarios(prev => prev.filter(s => s.id !== id))
        if (selectedScenario === id) {
          setSelectedScenario(null)
        }
      }
      return deleted
    },
    [selectedScenario]
  )

  return {
    scenarios,
    serverScenarios,
    customScenarios,
    selectedScenario,
    setSelectedScenario,
    loading,
    getCustomScenario,
    addCustomScenario,
    updateCustomScenario,
    deleteCustomScenario,
  }
}
