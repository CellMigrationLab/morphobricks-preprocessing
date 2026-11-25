# Cellpose stepper: human test in Google Colab

A click-through of the redesigned segmentation widget (cell 4 of
`MorphoBricks_Preprocessing_Cellpose.ipynb`). About 20 minutes on a GPU runtime. Tick each box;
note anything that looks wrong next to it.

## Set up

1. Open the notebook from the PR branch in Colab:
   `https://colab.research.google.com/github/CellMigrationLab/morphobricks-preprocessing/blob/claude/cellpose-stepper/notebooks/MorphoBricks_Preprocessing_Cellpose.ipynb`
2. Runtime > Change runtime type > a **GPU**.
3. In cell 1, show the code and set `RELEASE_REF` to the PR's head commit (the 40-character SHA
   on the PR page). Do not save the notebook. Run cells 1 and 2.
4. Run cell 3 and run preprocessing on a small 2D experiment with a nuclear and a cell stain.
   Give a masks folder **outside** the experiment and the output folder (or none).
5. Run cell 4. The first time, tick **Install Cellpose 4.2.1.1 (with PyTorch) now** and press
   the button. Restart and rerun cells 1, 3, 4 if it asks.

## Layout

- [ ] One step is shown at a time, with a strip of four chips at the top (**1 Folders**,
      **2 Output and channels**, **3 Model and preview**, **4 Segment**) and **Back** / **Next**
      buttons, as in the preprocessing app. No large empty areas.
- [ ] There is no *What to do next* section anywhere, in cell 4 or below it.

## Step 1, Folders

- [ ] The run folder and masks folder are filled from cell 3. With both, the run opens at once
      and a green card says *Run opened: N images*.
- [ ] Below it, the run's images appear in the same image viewer as preprocessing (condition,
      repeat and image dropdowns), each channel captioned with its channel ID. Choose another
      image: it is drawn.
- [ ] Type the **run folder itself** as the masks folder and press **Open the run**: a red card
      refuses it (`output_inside_masks`), no images are shown, the chip has no check mark.
- [ ] Type a folder **inside the experiment** as the masks folder: refused
      (`masks_inside_experiment`). Type a folder **inside the run folder**: refused.
- [ ] Put the right masks folder back, press **Open the run**, then **Next**.

## Step 2, Output and channels

- [ ] Next to the Output choice, three lines say what each option writes and reads, in full
      (not cut to one line): Cell mask, Nuclei mask, Both (two mask types, same model unless the
      nuclei get their own in step 3).
- [ ] Press **Choose the channels**, pick a cell and a nuclear channel by ID.
- [ ] Press **Back**, then **Next**: the output, mask type and channels are still as you left
      them.

## Step 3, Model and preview

- [ ] There is no **Load the model** button. Each setting (diameter, flow threshold, cell
      probability threshold, batch size) has a one-line help beside it saying which way to move
      it.
- [ ] Leave the device on `cuda`. If the model's weights are not on this runtime yet, press
      **Preview** without the download tick: it refuses and says to allow the download.
- [ ] Tick **Allow the weights download** and press **Preview**: the download shows as **one**
      progress bar (no stream of log lines), then a green *Model ... loaded on cuda* card and the
      preview figures for the image in the dropdown.
- [ ] Choose another image and press **Preview**: no loading card appears this time (the model is
      reused); the preview is of the new image.
- [ ] Change the diameter and press **Preview**: still no reload; the new diameter is used.
- [ ] Change the model in the dropdown: the step 3 chip loses its check mark until the next
      **Preview**.
- [ ] Change the model (for example to `cpsam`) and press **Preview**: it loads again (a new
      loading card), then previews.
- [ ] Optional, GPU check: switch to a CPU runtime and keep `cuda`: **Preview** refuses
      (*Nothing falls back to the CPU*); nothing loads on the CPU.
- [ ] Optional, Both: go back to step 2, choose **Both**, choose the channels again, come back:
      a visible box **Output Both: the nuclei** offers a separate nuclei model and settings.

## Step 4, Segment

- [ ] On arriving, a line says exactly what Segment will use (model, device, diameter, flow,
      cellprob, batch). Go back, change the diameter without previewing, come back: the line is
      amber and names the change (Segment is still allowed).
- [ ] Press **Segment the whole experiment**: no loading card (the preview's model is reused),
      a progress line, then the table of every image.
- [ ] Under the table, one line: *Next: run cell 5 (Register the masks) to declare
      [masks.Cells] and describe the masks.*
- [ ] All four chips have a check mark.
- [ ] Run cell 5 and register the masks as before: nothing about the records changed.
