import type { LifeData, Load } from "../types";
import { Section, Source, SpeciesGrid } from "./ui";

export function LifeTab({ life }: { life: Load<LifeData> }) {
  return (
    <>
      <Section title="Native plants" load={life} loading="Asking iNaturalist who lives here…"
        note={life.state === "ok" ? <>Most-recorded native plants in {life.data.county.name}.</> : undefined}>
        {(d: LifeData) => <SpeciesGrid species={d.native_plants} />}
      </Section>

      {life.state === "ok" && (
        <>
          <Section title="First to bloom" note="Native plants recorded flowering in February and March: the start of the year here.">
            <SpeciesGrid species={life.data.early_bloomers} />
          </Section>

          <Section title="Introduced plants to watch" note="The most-recorded non-native plants in the county. Several are invasive; removing them is restoration work anyone can do.">
            <SpeciesGrid species={life.data.introduced_plants} />
          </Section>

          {life.data.birds && (
            <Section title="Birds through the year" note="Grouped by whether people report them in January, June, or both. A rough guide: rarely reported birds can land in the wrong group.">
              <h3>Year-round</h3>
              <SpeciesGrid species={life.data.birds.year_round} />
              <h3>Summer visitors</h3>
              <SpeciesGrid species={life.data.birds.summer} />
              <h3>Winter visitors</h3>
              <SpeciesGrid species={life.data.birds.winter} />
            </Section>
          )}

          {life.data.fish.length > 0 && (
            <Section title="Fish along your water's route" note="Fish recorded within 10 km of points your water passes, from your creek to the coast.">
              {life.data.fish.map((f) => (
                <div key={f.segment} className="route-fish">
                  <h3>{f.segment}</h3>
                  <SpeciesGrid species={f.species} />
                </div>
              ))}
            </Section>
          )}
          <Source>{life.data.source}. Counts are observations, not population sizes: they track where people look as much as what lives there.</Source>
        </>
      )}
    </>
  );
}
