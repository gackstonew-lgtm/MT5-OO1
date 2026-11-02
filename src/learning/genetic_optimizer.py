"""
Genetic Algorithm Strategy Parameter Optimizer.
Explores multidimensional parameter spaces (lookback periods, scoring weights,
OTE thresholds, risk multipliers) using evolutionary selection, crossover, and mutation.
"""

from __future__ import annotations
import random
from dataclasses import dataclass
from typing import List, Dict, Any, Callable, Tuple


@dataclass
class Individual:
    genes: Dict[str, Any]
    fitness: float = 0.0


class GeneticOptimizer:
    def __init__(
        self,
        param_bounds: Dict[str, List[Any]],
        population_size: int = 20,
        mutation_rate: float = 0.15,
        crossover_rate: float = 0.70,
        generations: int = 10
    ):
        self.param_bounds = param_bounds
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.generations = generations

    def _create_random_individual(self) -> Individual:
        genes = {}
        for param, choices in self.param_bounds.items():
            genes[param] = random.choice(choices)
        return Individual(genes=genes)

    def optimize(
        self,
        fitness_function: Callable[[Dict[str, Any]], float]
    ) -> Tuple[Dict[str, Any], float]:
        """
        Executes evolutionary search across parameter bounds.
        Returns (best_genes, best_fitness).
        """
        # 1. Initialize population
        population: List[Individual] = [
            self._create_random_individual() for _ in range(self.population_size)
        ]

        best_overall = population[0]

        for gen in range(self.generations):
            # Evaluate fitness
            for ind in population:
                ind.fitness = fitness_function(ind.genes)
                if ind.fitness > best_overall.fitness:
                    best_overall = ind

            # Sort by fitness descending
            population.sort(key=lambda x: x.fitness, reverse=True)

            # Elite selection (keep top 20%)
            elite_count = max(2, int(self.population_size * 0.2))
            next_generation = population[:elite_count]

            # Reproduction
            while len(next_generation) < self.population_size:
                # Tournament selection
                parent_a = self._tournament_select(population)
                parent_b = self._tournament_select(population)

                # Crossover
                if random.random() < self.crossover_rate:
                    child_genes = self._crossover(parent_a.genes, parent_b.genes)
                else:
                    child_genes = dict(parent_a.genes)

                # Mutation
                child_genes = self._mutate(child_genes)
                next_generation.append(Individual(genes=child_genes))

            population = next_generation

        return best_overall.genes, best_overall.fitness

    def _tournament_select(self, population: List[Individual], k: int = 3) -> Individual:
        tournament = random.sample(population, min(k, len(population)))
        tournament.sort(key=lambda x: x.fitness, reverse=True)
        return tournament[0]

    def _crossover(self, parent_a: Dict[str, Any], parent_b: Dict[str, Any]) -> Dict[str, Any]:
        child = {}
        for k in parent_a:
            child[k] = parent_a[k] if random.random() < 0.5 else parent_b[k]
        return child

    def _mutate(self, genes: Dict[str, Any]) -> Dict[str, Any]:
        mutated = dict(genes)
        for k, choices in self.param_bounds.items():
            if random.random() < self.mutation_rate:
                mutated[k] = random.choice(choices)
        return mutated
