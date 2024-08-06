# Copyright 2023-present, Argilla, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from datasets import (
    Dataset,
    DatasetInfo,
    IterableDataset,
    get_dataset_infos,
    load_dataset,
    load_from_disk,
)
from pydantic import Field, PrivateAttr
from typing_extensions import override
from upath import UPath

from distilabel.distiset import Distiset
from distilabel.mixins.runtime_parameters import RuntimeParameter
from distilabel.steps.base import GeneratorStep

if TYPE_CHECKING:
    from distilabel.steps.typing import GeneratorStepOutput


class LoadDataFromHub(GeneratorStep):
    """Loads a dataset from the Hugging Face Hub.

    `GeneratorStep` that loads a dataset from the Hugging Face Hub using the `datasets`
    library.

    Attributes:
        repo_id: The Hugging Face Hub repository ID of the dataset to load.
        split: The split of the dataset to load.
        config: The configuration of the dataset to load. This is optional and only needed
            if the dataset has multiple configurations.

    Runtime parameters:
        - `batch_size`: The batch size to use when processing the data.
        - `repo_id`: The Hugging Face Hub repository ID of the dataset to load.
        - `split`: The split of the dataset to load. Defaults to 'train'.
        - `config`: The configuration of the dataset to load. This is optional and only
            needed if the dataset has multiple configurations.
        - `streaming`: Whether to load the dataset in streaming mode or not. Defaults to
            `False`.
        - `num_examples`: The number of examples to load from the dataset.
            By default will load all examples.
        - `storage_options`: Key/value pairs to be passed on to the file-system backend, if any.
            Defaults to `None`.

    Output columns:
        - dynamic (`all`): The columns that will be generated by this step, based on the
            datasets loaded from the Hugging Face Hub.

    Categories:
        - load

    Examples:

        Load data from a dataset in Hugging Face Hub:

        ```python
        from distilabel.steps import LoadDataFromHub

        loader = LoadDataFromHub(
            repo_id="distilabel-internal-testing/instruction-dataset-mini",
            split="test",
            batch_size=2
        )
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'prompt': 'Arianna has 12...', False)
        ```
    """

    repo_id: RuntimeParameter[str] = Field(
        default=None,
        description="The Hugging Face Hub repository ID of the dataset to load.",
    )
    split: RuntimeParameter[str] = Field(
        default="train",
        description="The split of the dataset to load. Defaults to 'train'.",
    )
    config: Optional[RuntimeParameter[str]] = Field(
        default=None,
        description="The configuration of the dataset to load. This is optional and only"
        " needed if the dataset has multiple configurations.",
    )
    streaming: RuntimeParameter[bool] = Field(
        default=False,
        description="Whether to load the dataset in streaming mode or not. Defaults to False.",
    )
    num_examples: Optional[RuntimeParameter[int]] = Field(
        default=None,
        description="The number of examples to load from the dataset. By default will load all examples.",
    )
    storage_options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="The storage options to use when loading the dataset.",
    )

    outputs: List[str] = Field(default_factory=list)

    _dataset: Union[IterableDataset, Dataset, None] = PrivateAttr(None)

    @override
    def model_post_init(self, __context: Any) -> None:
        """Override this method to perform additional initialization after `__init__` and `model_construct`.
        This is useful if you want to do some validation that requires the entire model to be initialized.
        """
        super().model_post_init(__context)

    def load(self) -> None:
        """Load the dataset from the Hugging Face Hub"""
        super().load()

        if self._dataset is not None:
            self.outputs = self._get_dataset_columns()
            # Here to simplify the functionality of distilabel.steps.generators.util.make_generator_step
            return

        self._dataset = load_dataset(
            self.repo_id,  # type: ignore
            self.config,
            split=self.split,
            streaming=self.streaming,
        )
        num_examples = self._get_dataset_num_examples()
        self.num_examples = (
            min(self.num_examples, num_examples) if self.num_examples else num_examples
        )

        if not self.streaming:
            self._dataset = self._dataset.select(range(self.num_examples))
        self.outputs = self._get_dataset_columns()


    def process(self, offset: int = 0) -> "GeneratorStepOutput":
        """Yields batches from the loaded dataset from the Hugging Face Hub.

        Args:
            offset: The offset to start yielding the data from. Will be used during the caching
                process to help skipping already processed data.

        Yields:
            A tuple containing a batch of rows and a boolean indicating if the batch is
            the last one.
        """
        num_returned_rows = 0
        for batch_num, batch in enumerate(
            self._dataset.iter(batch_size=self.batch_size)  # type: ignore
        ):
            if batch_num * self.batch_size < offset:
                continue
            transformed_batch = self._transform_batch(batch)
            batch_size = len(transformed_batch)
            num_returned_rows += batch_size
            yield transformed_batch, num_returned_rows >= self.num_examples


    def _transform_batch(self, batch: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Transform a batch of data from the Hugging Face Hub into a list of rows.

        Args:
            batch: The batch of data from the Hugging Face Hub.

        Returns:
            A list of rows, where each row is a dictionary of column names and values.
        """
        length = len(next(iter(batch.values())))
        rows = []
        for i in range(length):
            rows.append({col: values[i] for col, values in batch.items()})
        return rows

    def _get_dataset_num_examples(self) -> int:
        """Get the number of examples in the dataset, based on the `split` and `config`
        runtime parameters provided.

        Returns:
            The number of examples in the dataset.
        """
        return (
            self._dataset_info[self.config if self.config else "default"]
            .splits[self.split]
            .num_examples
        )

    def _get_dataset_columns(self) -> List[str]:
        """Get the columns of the dataset, based on the `config` runtime parameter provided.

        Returns:
            The columns of the dataset.
        """
        return list(
            self._dataset_info[
                self.config if self.config else "default"
            ].features.keys()
        )

    @cached_property
    def _dataset_info(self) -> Dict[str, DatasetInfo]:
        """Calls the Datasets Server API from Hugging Face to obtain the dataset information.

        Returns:
            The dataset information.
        """
        repo_id = self.repo_id
        config = self.config

        try:
            return get_dataset_infos(repo_id)
        except Exception as e:
            # The previous could fail in case of a internet connection issues.
            # Assuming the dataset is already loaded and we can get the info from the loaded dataset, otherwise it will fail anyway.
            self._logger.warning(
                f"Failed to get dataset info from Hugging Face Hub, trying to get it loading the dataset. Error: {e}"
            )
            ds = load_dataset(repo_id, config=self.config, split=self.split)
            if config:
                return ds[config].info
            return ds.info


class LoadDataFromFileSystem(LoadDataFromHub):
    """Loads a dataset from a file in your filesystem.

    `GeneratorStep` that creates a dataset from a file in the filesystem, uses Hugging Face `datasets`
    library. Take a look at [Hugging Face Datasets](https://huggingface.co/docs/datasets/loading)
    for more information of the supported file types.

    Attributes:
        data_files: The path to the file, or directory containing the files that conform
            the dataset.
        split: The split of the dataset to load (typically will be `train`, `test` or `validation`).

    Runtime parameters:
        - `batch_size`: The batch size to use when processing the data.
        - `data_files`: The path to the file, or directory containing the files that conform
            the dataset.
        - `split`: The split of the dataset to load. Defaults to 'train'.
        - `streaming`: Whether to load the dataset in streaming mode or not. Defaults to
            `False`.
        - `num_examples`: The number of examples to load from the dataset.
            By default will load all examples.
        - `storage_options`: Key/value pairs to be passed on to the file-system backend, if any.
            Defaults to `None`.
        - `filetype`: The expected filetype. If not provided, it will be inferred from the file extension.
            For more than one file, it will be inferred from the first file.

    Output columns:
        - dynamic (`all`): The columns that will be generated by this step, based on the
            datasets loaded from the Hugging Face Hub.

    Categories:
        - load

    Examples:

        Load data from a Hugging Face dataset in your file system:

        ```python
        from distilabel.steps import LoadDataFromFileSystem

        loader = LoadDataFromFileSystem(data_files="path/to/dataset.jsonl")
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'type': 'function', 'function':...', False)
        ```

        Specify a filetype if the file extension is not expected:

        ```python
        from distilabel.steps import LoadDataFromFileSystem

        loader = LoadDataFromFileSystem(filetype="csv", data_files="path/to/dataset.txtr")
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'type': 'function', 'function':...', False)
        ```

        Load data from a file in your cloud provider:

        ```python
        from distilabel.steps import LoadDataFromFileSystem

        loader = LoadDataFromFileSystem(
            data_files="gcs://path/to/dataset",
            storage_options={"project": "experiments-0001"}
        )
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'type': 'function', 'function':...', False)
        ```
    """

    data_files: RuntimeParameter[Union[str, Path]] = Field(
        default=None,
        description="The data files, or directory containing the data files, to generate the dataset from.",
    )
    filetype: Optional[RuntimeParameter[str]] = Field(
        default=None,
        description="The expected filetype. If not provided, it will be inferred from the file extension.",
    )

    def load(self) -> None:
        """Load the dataset from the file/s in disk."""
        GeneratorStep.load(self)

        data_path = UPath(self.data_files, storage_options=self.storage_options)

        (data_files, self.filetype) = self._prepare_data_files(data_path)

        self._dataset = load_dataset(
            self.filetype,
            data_files=data_files,
            split=self.split,
            streaming=self.streaming,
            storage_options=self.storage_options,
        )

        if not self.streaming and self.num_examples:
            self._dataset = self._dataset.select(range(self.num_examples))
        if not self.num_examples:
            if self.streaming:
                # There's no better way to get the number of examples in a streaming dataset,
                # load it again for the moment.
                self.num_examples = len(
                    load_dataset(
                        self.filetype, data_files=self.data_files, split=self.split
                    )
                )
            else:
                self.num_examples = len(self._dataset)
        self.outputs = self._dataset.column_names

    @staticmethod
    def _prepare_data_files(
        data_path: UPath,
    ) -> Tuple[Union[str, Sequence[str], Mapping[str, Union[str, Sequence[str]]]], str]:
        """Prepare the loading process by setting the `data_files` attribute.

        Args:
            data_path: The path to the data files, or directory containing the data files.

        Returns:
            Tuple with the data files and the filetype.
        """

        def get_filetype(data_path: UPath) -> str:
            filetype = data_path.suffix.lstrip(".")
            if filetype == "jsonl":
                filetype = "json"
            return filetype

        if data_path.is_file():
            filetype = get_filetype(data_path)
            data_files = str(data_path)
        elif data_path.is_dir():
            file_sequence = []
            file_map = defaultdict(list)
            for file_or_folder in data_path.iterdir():
                if file_or_folder.is_file():
                    file_sequence.append(str(file_or_folder))
                elif file_or_folder.is_dir():
                    for file in file_or_folder.iterdir():
                        file_sequence.append(str(file))
                        file_map[str(file_or_folder)].append(str(file))

            data_files = file_sequence or file_map
            # Try to obtain the filetype from any of the files, assuming all files have the same type.
            if file_sequence:
                filetype = get_filetype(UPath(file_sequence[0]))
            else:
                filetype = get_filetype(UPath(file_map[list(file_map.keys())[0]][0]))
        return data_files, filetype


class LoadDataFromDisk(LoadDataFromHub):
    """Load a dataset that was previously saved to disk.

    If you previously saved your dataset using the `save_to_disk` method, or
    `Distiset.save_to_disk` you can load it again to build a new pipeline using this class.

    Attributes:
        dataset_path: The path to the dataset or distiset.
        split: The split of the dataset to load (typically will be `train`, `test` or `validation`).
        config: The configuration of the dataset to load. This is optional and only needed
            if the dataset has multiple configurations.

    Runtime parameters:
        - `batch_size`: The batch size to use when processing the data.
        - `dataset_path`: The path to the dataset or distiset.
        - `is_distiset`: Whether the dataset to load is a `Distiset` or not. Defaults to False.
        - `split`: The split of the dataset to load. Defaults to 'train'.
        - `config`: The configuration of the dataset to load. This is optional and only
            needed if the dataset has multiple configurations.
        - `num_examples`: The number of examples to load from the dataset.
            By default will load all examples.
        - `storage_options`: Key/value pairs to be passed on to the file-system backend, if any.
            Defaults to `None`.

    Output columns:
        - dynamic (`all`): The columns that will be generated by this step, based on the
            datasets loaded from the Hugging Face Hub.

    Categories:
        - load

    Examples:

        Load data from a Hugging Face Dataset:

        ```python
        from distilabel.steps import LoadDataFromDisk

        loader = LoadDataFromDisk(dataset_path="path/to/dataset")
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'type': 'function', 'function':...', False)
        ```

        Load data from a distilabel Distiset:

        ```python
        from distilabel.steps import LoadDataFromDisk

        # Specify the configuration to load.
        loader = LoadDataFromDisk(
            dataset_path="path/to/dataset",
            is_distiset=True,
            config="leaf_step_1"
        )
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'a': 1}, {'a': 2}, {'a': 3}], True)
        ```

        Load data from a Hugging Face Dataset or Distiset in your cloud provider:

        ```python
        from distilabel.steps import LoadDataFromDisk

        loader = LoadDataFromDisk(
            dataset_path="gcs://path/to/dataset",
            storage_options={"project": "experiments-0001"}
        )
        loader.load()

        # Just like we saw with LoadDataFromDicts, the `process` method will yield batches.
        result = next(loader.process())
        # >>> result
        # ([{'type': 'function', 'function':...', False)
        ```
    """

    dataset_path: RuntimeParameter[Union[str, Path]] = Field(
        default=None,
        description="Path to the dataset or distiset.",
    )
    config: RuntimeParameter[str] = Field(
        default=None,
        description="The configuration of the dataset to load. This is optional and only"
        " needed if the dataset has multiple configurations.",
    )
    is_distiset: Optional[RuntimeParameter[bool]] = Field(
        default=False,
        description="Whether the dataset to load is a `Distiset` or not. Defaults to False.",
    )
    keep_in_memory: Optional[RuntimeParameter[bool]] = Field(
        default=None,
        description="Whether to copy the dataset in-memory, see `datasets.Dataset.load_from_disk` "
        " for more information. Defaults to `None`.",
    )
    split: Optional[RuntimeParameter[str]] = Field(
        default=None,
        description="The split of the dataset to load. By default will load the whole Dataset/Distiset.",
    )

    def load(self) -> None:
        """Load the dataset from the file/s in disk."""
        super(GeneratorStep, self).load()
        if self.is_distiset:
            ds = Distiset.load_from_disk(
                self.dataset_path,
                keep_in_memory=self.keep_in_memory,
                storage_options=self.storage_options,
            )
            if self.config:
                ds = ds[self.config]

        else:
            ds = load_from_disk(
                self.dataset_path,
                keep_in_memory=self.keep_in_memory,
                storage_options=self.storage_options,
            )

        if self.split:
            ds = ds[self.split]

        self._dataset = ds

        if self.num_examples:
            self._dataset = self._dataset.select(range(self.num_examples))
        else:
            self.num_examples = len(self._dataset)
        self.outputs = self._dataset.column_names


