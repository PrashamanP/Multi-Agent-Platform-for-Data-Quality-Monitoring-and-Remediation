"""
File handling utilities for the DQ system.
"""

import pandas as pd
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List, Iterator
import logging
import os


logger = logging.getLogger(__name__)


class FileHandler:
    """Handles file I/O operations for datasets."""
    
    @staticmethod
    def load_dataset(file_path: str, focused_columns_only: bool = True, **kwargs) -> pd.DataFrame:
        """
        Load dataset from various file formats.
        
        Args:
            file_path: Path to the dataset file
            focused_columns_only: If True, only load the focused columns defined in config
            **kwargs: Additional parameters for pandas read functions
            
        Returns:
            Loaded DataFrame with optionally filtered columns
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"Dataset file not found: {file_path}")
        
        file_extension = path.suffix.lower()
        
        try:
            if file_extension == '.csv':
                df = FileHandler._load_csv(path, **kwargs)
            elif file_extension == '.parquet':
                df = FileHandler._load_parquet(path, **kwargs)
            elif file_extension in ['.xlsx', '.xls']:
                df = FileHandler._load_excel(path, **kwargs)
            else:
                raise ValueError(f"Unsupported file format: {file_extension}")
            
            # Filter columns if requested
            if focused_columns_only:
                df = FileHandler._filter_focused_columns(df)
                
            return df
                
        except Exception as e:
            logger.error(f"Failed to load dataset from {file_path}: {str(e)}")
            raise
    
    @staticmethod
    def load_dataset_chunks(file_path: str, chunk_size: int = 50000, 
                           focused_columns_only: bool = True, **kwargs) -> Iterator[pd.DataFrame]:
        """
        Load dataset in chunks for memory-efficient processing of large files.
        
        Args:
            file_path: Path to the dataset file
            chunk_size: Number of rows per chunk
            focused_columns_only: If True, only load the focused columns defined in config
            **kwargs: Additional parameters for pandas read functions
            
        Yields:
            DataFrame chunks
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"Dataset file not found: {file_path}")
        
        file_extension = path.suffix.lower()
        
        try:
            if file_extension == '.csv':
                chunk_iterator = FileHandler._load_csv_chunks(path, chunk_size, **kwargs)
            elif file_extension == '.parquet':
                # For parquet, we need to load in chunks manually
                chunk_iterator = FileHandler._load_parquet_chunks(path, chunk_size, **kwargs)
            elif file_extension in ['.xlsx', '.xls']:
                # Excel files are loaded entirely due to format limitations
                df = FileHandler._load_excel(path, **kwargs)
                chunk_iterator = FileHandler._split_dataframe_chunks(df, chunk_size)
            else:
                raise ValueError(f"Unsupported file format: {file_extension}")
            
            # Filter columns if requested
            if focused_columns_only:
                focused_columns = FileHandler._get_focused_columns()
                for chunk in chunk_iterator:
                    available_columns = [col for col in focused_columns if col in chunk.columns]
                    if available_columns:
                        yield chunk[available_columns]
                    else:
                        logger.warning("No focused columns found in chunk, returning original chunk")
                        yield chunk
            else:
                for chunk in chunk_iterator:
                    yield chunk
                
        except Exception as e:
            logger.error(f"Failed to load dataset chunks from {file_path}: {str(e)}")
            raise
    
    @staticmethod
    def get_file_size_mb(file_path: str) -> float:
        """Get file size in MB."""
        return os.path.getsize(file_path) / (1024 * 1024)
    
    @staticmethod
    def _filter_focused_columns(df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter DataFrame to include only the focused columns defined in config.
        
        Args:
            df: Input DataFrame
            
        Returns:
            Filtered DataFrame with only focused columns
        """
        try:
            # Load focused columns configuration
            config_path = Path(__file__).parent.parent.parent / "config" / "columns.yaml"
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            
            focused_columns = config.get('focused_columns', [])
            
            # Check which focused columns exist in the DataFrame
            available_columns = [col for col in focused_columns if col in df.columns]
            missing_columns = [col for col in focused_columns if col not in df.columns]
            
            if missing_columns:
                logger.warning(f"Missing focused columns in dataset: {missing_columns}")
            
            if not available_columns:
                logger.warning("No focused columns found in dataset, returning original DataFrame")
                return df
            
            logger.info(f"Filtering dataset to {len(available_columns)} focused columns")
            return df[available_columns]
            
        except Exception as e:
            logger.error(f"Failed to filter focused columns: {str(e)}")
            logger.warning("Returning original DataFrame without filtering")
            return df
    
    @staticmethod
    def _load_csv(path: Path, **kwargs) -> pd.DataFrame:
        """Load CSV file with encoding detection."""
        # Default CSV parameters
        csv_params = {
            'low_memory': False,
            'encoding': 'utf-8'
        }
        csv_params.update(kwargs)
        
        try:
            return pd.read_csv(path, **csv_params)
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 encoding failed for {path}, trying latin1")
            csv_params['encoding'] = 'latin1'
            return pd.read_csv(path, **csv_params)
    
    @staticmethod
    def _load_parquet(path: Path, **kwargs) -> pd.DataFrame:
        """Load Parquet file."""
        return pd.read_parquet(path, **kwargs)
    
    @staticmethod
    def _load_excel(path: Path, **kwargs) -> pd.DataFrame:
        """Load Excel file."""
        return pd.read_excel(path, **kwargs)
    
    @staticmethod
    def _load_csv_chunks(path: Path, chunk_size: int, **kwargs) -> Iterator[pd.DataFrame]:
        """Load CSV file in chunks."""
        # Default CSV parameters
        csv_params = {
            'low_memory': False,
            'encoding': 'utf-8',
            'chunksize': chunk_size
        }
        csv_params.update(kwargs)
        
        try:
            return pd.read_csv(path, **csv_params)
        except UnicodeDecodeError:
            logger.warning(f"UTF-8 encoding failed for {path}, trying latin1")
            csv_params['encoding'] = 'latin1'
            return pd.read_csv(path, **csv_params)
    
    @staticmethod
    def _load_parquet_chunks(path: Path, chunk_size: int, **kwargs) -> Iterator[pd.DataFrame]:
        """Load Parquet file in chunks."""
        # Load entire parquet file and split into chunks
        df = pd.read_parquet(path, **kwargs)
        return FileHandler._split_dataframe_chunks(df, chunk_size)
    
    @staticmethod
    def _split_dataframe_chunks(df: pd.DataFrame, chunk_size: int) -> Iterator[pd.DataFrame]:
        """Split DataFrame into chunks."""
        for i in range(0, len(df), chunk_size):
            yield df.iloc[i:i + chunk_size]
    
    @staticmethod
    def _get_focused_columns() -> List[str]:
        """Get focused columns from config."""
        try:
            config_path = Path(__file__).parent.parent.parent / "config" / "columns.yaml"
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            return config.get('focused_columns', [])
        except Exception as e:
            logger.error(f"Failed to load focused columns config: {str(e)}")
            return []

